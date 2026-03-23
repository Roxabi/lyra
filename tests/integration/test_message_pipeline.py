"""Integration tests for MessagePipeline that emit structured traces.

Each test instruments MessagePipeline with a trace hook and writes a
JSON trace to tests/data/traces/<test_name>.json for local debugging.

Traces are gitignored (see .gitignore) but provide an instant record of
what the pipeline actually did when a regression is being investigated.

Trace format::

    {
        "test": "<test_name>",
        "steps": [
            {"stage": "inbound", "event": "message_received", ...},
            {"stage": "pool",    "event": "agent_selected", ...},
            ...
        ]
    }
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lyra.core.hub.message_pipeline import Action, MessagePipeline
from lyra.core.message import Platform
from tests.core.conftest import (
    _make_hub,
    _MockAdapter,
    make_inbound_message,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TRACES_DIR = Path(__file__).resolve().parent.parent / "data" / "traces"


def _make_trace_hook(steps: list[dict[str, Any]]) -> Any:
    """Return a callable that appends each trace event to *steps*."""

    def _hook(stage: str, event: str, **payload: object) -> None:
        steps.append({"stage": stage, "event": event, **payload})

    return _hook


def _write_trace(test_name: str, steps: list[dict[str, Any]]) -> None:
    """Write *steps* as a JSON trace file to tests/data/traces/."""
    _TRACES_DIR.mkdir(parents=True, exist_ok=True)
    trace_path = _TRACES_DIR / f"{test_name}.json"
    trace = {"test": test_name, "steps": steps}
    trace_path.write_text(json.dumps(trace, indent=2, default=str))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMessagePipelineTraces:
    """Integration tests that verify pipeline routing and emit traces."""

    async def test_happy_path_submit_to_pool(self) -> None:
        """Normal message → agent selected → submitted to pool."""
        test_name = "test_happy_path_submit_to_pool"
        steps: list[dict[str, Any]] = []

        hub = _make_hub()
        pipeline = MessagePipeline(hub, trace_hook=_make_trace_hook(steps))
        msg = make_inbound_message()

        result = await pipeline.process(msg)

        _write_trace(test_name, steps)

        assert result.action == Action.SUBMIT_TO_POOL

        # Verify expected trace events in order
        events = [(s["stage"], s["event"]) for s in steps]
        assert ("inbound", "message_received") in events
        assert ("pool", "agent_selected") in events
        assert ("outbound", "message_submitted") in events

        # message_received carries platform and user_id
        received = next(s for s in steps if s["event"] == "message_received")
        assert received["platform"] == "telegram"
        assert received["user_id"] == "alice"

        # agent_selected names the agent
        selected = next(s for s in steps if s["event"] == "agent_selected")
        assert selected["agent"] == "lyra"

    async def test_unknown_platform_drops_with_trace(self) -> None:
        """Unknown platform → DROP recorded in trace."""
        test_name = "test_unknown_platform_drops_with_trace"
        steps: list[dict[str, Any]] = []

        hub = _make_hub()
        pipeline = MessagePipeline(hub, trace_hook=_make_trace_hook(steps))
        msg = make_inbound_message(platform="unknown_plat")

        result = await pipeline.process(msg)

        _write_trace(test_name, steps)

        assert result.action == Action.DROP

        events = [(s["stage"], s["event"]) for s in steps]
        assert ("inbound", "message_received") in events
        assert ("inbound", "platform_invalid") in events

        invalid = next(s for s in steps if s["event"] == "platform_invalid")
        assert invalid["action"] == Action.DROP.value

    async def test_rate_limited_drops_with_trace(self) -> None:
        """Rate-limited message → DROP after passing first message."""
        test_name = "test_rate_limited_drops_with_trace"
        steps_first: list[dict[str, Any]] = []
        steps_second: list[dict[str, Any]] = []

        hub = _make_hub(rate_limit=1, rate_window=60)
        msg = make_inbound_message()

        pipeline1 = MessagePipeline(hub, trace_hook=_make_trace_hook(steps_first))
        r1 = await pipeline1.process(msg)
        _write_trace(f"{test_name}_pass", steps_first)

        pipeline2 = MessagePipeline(hub, trace_hook=_make_trace_hook(steps_second))
        r2 = await pipeline2.process(msg)
        _write_trace(f"{test_name}_drop", steps_second)

        assert r1.action == Action.SUBMIT_TO_POOL
        assert r2.action == Action.DROP

        drop_events = [(s["stage"], s["event"]) for s in steps_second]
        assert ("inbound", "rate_limited") in drop_events

    async def test_no_binding_drops_with_trace(self) -> None:
        """No binding → DROP after platform validation."""
        from lyra.core.hub import Hub

        test_name = "test_no_binding_drops_with_trace"
        steps: list[dict[str, Any]] = []

        hub = Hub()
        adapter = _MockAdapter()
        hub.register_adapter(Platform.TELEGRAM, "main", adapter)  # type: ignore[arg-type]
        # Intentionally no binding registered

        pipeline = MessagePipeline(hub, trace_hook=_make_trace_hook(steps))
        msg = make_inbound_message()

        result = await pipeline.process(msg)

        _write_trace(test_name, steps)

        assert result.action == Action.DROP

        events = [(s["stage"], s["event"]) for s in steps]
        assert ("inbound", "message_received") in events
        assert ("pool", "no_binding") in events

    async def test_no_agent_drops_with_trace(self) -> None:
        """Binding points to missing agent → DROP with trace."""
        from lyra.core.hub import Hub

        test_name = "test_no_agent_drops_with_trace"
        steps: list[dict[str, Any]] = []

        hub = Hub()
        adapter = _MockAdapter()
        hub.register_adapter(Platform.TELEGRAM, "main", adapter)  # type: ignore[arg-type]
        hub.register_binding(
            Platform.TELEGRAM, "main", "*", "ghost_agent", "telegram:main:*"
        )
        # "ghost_agent" is never registered → no agent found

        pipeline = MessagePipeline(hub, trace_hook=_make_trace_hook(steps))
        msg = make_inbound_message()

        result = await pipeline.process(msg)

        _write_trace(test_name, steps)

        assert result.action == Action.DROP

        events = [(s["stage"], s["event"]) for s in steps]
        assert ("pool", "no_agent") in events

        no_agent = next(s for s in steps if s["event"] == "no_agent")
        assert no_agent["agent_name"] == "ghost_agent"

    async def test_trace_hook_is_optional(self) -> None:
        """Pipeline without trace_hook runs correctly with no overhead path."""
        hub = _make_hub()
        pipeline = MessagePipeline(hub)  # no trace_hook
        msg = make_inbound_message()

        result = await pipeline.process(msg)

        assert result.action == Action.SUBMIT_TO_POOL

    async def test_trace_hook_exception_is_swallowed(self) -> None:
        """A raising trace_hook must not abort pipeline processing."""

        def _bad_hook(stage: str, event: str, **payload: object) -> None:
            raise RuntimeError("trace hook bug")

        hub = _make_hub()
        pipeline = MessagePipeline(hub, trace_hook=_bad_hook)
        msg = make_inbound_message()

        # Must not raise despite the hook failing on every call
        result = await pipeline.process(msg)

        assert result.action == Action.SUBMIT_TO_POOL

    async def test_trace_file_is_written(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify the trace JSON file is created and valid."""
        # Redirect traces to tmp_path for test isolation
        import tests.integration.test_message_pipeline as mod

        monkeypatch.setattr(mod, "_TRACES_DIR", tmp_path)

        test_name = "test_trace_file_is_written"
        steps: list[dict[str, Any]] = []

        hub = _make_hub()
        pipeline = MessagePipeline(hub, trace_hook=_make_trace_hook(steps))
        msg = make_inbound_message()

        await pipeline.process(msg)
        _write_trace(test_name, steps)

        trace_file = tmp_path / f"{test_name}.json"
        assert trace_file.exists()

        data = json.loads(trace_file.read_text())
        assert data["test"] == test_name
        assert isinstance(data["steps"], list)
        assert len(data["steps"]) >= 3  # received, agent_selected, message_submitted
