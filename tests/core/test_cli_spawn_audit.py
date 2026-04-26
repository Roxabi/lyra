"""Tests for CLI spawn audit emission (#855) — S2 criteria."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lyra.core.agent.agent_config import ModelConfig
from lyra.core.cli.cli_pool import CliPool
from lyra.core.trace import TraceContext

from .conftest_cli_pool import make_fake_proc

_POOL_ID = "telegram:main:chat:99"
_MODEL = ModelConfig(model="claude-opus-4-6")

# Patch target for the subprocess call (asyncio is shared across the mixin).
_WORKER_PATCH = "lyra.core.cli.cli_pool_worker.asyncio.create_subprocess_exec"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sink() -> MagicMock:
    sink = MagicMock()
    sink.emit = AsyncMock()
    return sink


async def _drain_tasks() -> None:
    """Yield control so fire-and-forget tasks run to completion."""
    for _ in range(5):
        await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# AuditSink + _audit_tasks
# ---------------------------------------------------------------------------


class TestCliSpawnAuditEmit:
    async def test_no_audit_sink_spawn_succeeds_and_tasks_empty(self) -> None:
        """CliPool without audit_sink completes spawn with no error."""
        pool = CliPool()
        fake_proc = make_fake_proc([])

        with patch(_WORKER_PATCH, return_value=fake_proc):
            entry = await pool._spawn(_POOL_ID, _MODEL)

        assert entry is not None
        assert len(pool._audit_tasks) == 0

    async def test_spawn_emits_event_with_correct_pid(self) -> None:
        """SecurityEvent.pid must equal the spawned process PID."""
        sink = _make_sink()
        pool = CliPool(audit_sink=sink)
        fake_proc = make_fake_proc([])
        fake_proc.pid = 12345

        with patch(_WORKER_PATCH, return_value=fake_proc):
            entry = await pool._spawn(_POOL_ID, _MODEL)

        assert entry is not None
        await _drain_tasks()

        sink.emit.assert_awaited_once()
        event = sink.emit.call_args[0][0]
        assert event.pid == 12345

    async def test_spawn_emits_event_with_trace_id(self) -> None:
        """SecurityEvent.trace_id must equal TraceContext value at spawn time."""
        from contextvars import copy_context

        sink = _make_sink()
        pool = CliPool(audit_sink=sink)
        fake_proc = make_fake_proc([])

        captured_events: list[object] = []

        async def _capture(ev: object) -> None:
            captured_events.append(ev)

        sink.emit = _capture  # type: ignore[method-assign]

        async def _inner() -> None:
            TraceContext.set_trace_id("trace-test-xyz")
            with patch(_WORKER_PATCH, return_value=fake_proc):
                await pool._spawn(_POOL_ID, _MODEL)
            await _drain_tasks()

        ctx = copy_context()
        await ctx.run(_inner)

        assert len(captured_events) == 1
        assert captured_events[0].trace_id == "trace-test-xyz"  # type: ignore[union-attr]

    async def test_spawn_no_emit_when_process_exits_in_liveness_gate(self) -> None:
        """Process that exits within 100ms must NOT emit a SecurityEvent."""
        sink = _make_sink()
        pool = CliPool(audit_sink=sink)

        # Dead-on-arrival: returncode set at proc creation → wait() returns immediately.
        dead_proc = make_fake_proc([])
        dead_proc.returncode = 1  # already exited

        with patch(_WORKER_PATCH, return_value=dead_proc):
            entry = await pool._spawn(_POOL_ID, _MODEL)

        assert entry is None
        await _drain_tasks()
        sink.emit.assert_not_called()

    async def test_audit_tasks_set_clears_after_task_completes(self) -> None:
        """Done-callback must remove completed tasks — set must not grow unboundedly."""
        gate = asyncio.Event()

        async def _gated_emit(*_args: object) -> None:
            del _args
            await gate.wait()

        sink = MagicMock()
        sink.emit = _gated_emit  # type: ignore[method-assign]

        pool = CliPool(audit_sink=sink)
        fake_proc = make_fake_proc([])

        with patch(_WORKER_PATCH, return_value=fake_proc):
            await pool._spawn(_POOL_ID, _MODEL)

        # Task exists in the set before gate releases.
        await asyncio.sleep(0)
        assert len(pool._audit_tasks) == 1

        gate.set()
        await _drain_tasks()
        assert len(pool._audit_tasks) == 0

    async def test_spawn_emits_tools_restricted_false_when_no_tools(self) -> None:
        """tools_restricted=False and tools_allowlist=[] when no tools configured."""
        sink = _make_sink()
        pool = CliPool(audit_sink=sink)
        model = ModelConfig(tools=())
        fake_proc = make_fake_proc([])

        with patch(_WORKER_PATCH, return_value=fake_proc):
            await pool._spawn(_POOL_ID, model)

        await _drain_tasks()
        event = sink.emit.call_args[0][0]
        assert event.tools_restricted is False
        assert event.tools_allowlist == []

    async def test_spawn_emits_tools_restricted_true_when_tools_set(self) -> None:
        """tools_restricted=True and allowlist populated when tools configured."""
        sink = _make_sink()
        pool = CliPool(audit_sink=sink)
        model = ModelConfig(tools=("Read", "Bash"))
        fake_proc = make_fake_proc([])

        with patch(_WORKER_PATCH, return_value=fake_proc):
            await pool._spawn(_POOL_ID, model)

        await _drain_tasks()
        event = sink.emit.call_args[0][0]
        assert event.tools_restricted is True
        assert set(event.tools_allowlist) == {"Read", "Bash"}


# ---------------------------------------------------------------------------
# JetStreamAuditSink.emit() — never raises
# ---------------------------------------------------------------------------


class TestJetStreamAuditSinkEmit:
    async def test_emit_logs_warning_on_publish_failure(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """emit() must catch exceptions and log at WARNING — never raises."""
        from datetime import UTC, datetime

        from lyra.infrastructure.audit.jetstream_sink import JetStreamAuditSink
        from roxabi_contracts.audit import SecurityEvent
        from roxabi_contracts.envelope import CONTRACT_VERSION

        sink = JetStreamAuditSink()

        js = MagicMock()
        js.publish = AsyncMock(side_effect=RuntimeError("nats down"))
        sink._js = js  # type: ignore[attr-defined]

        event = SecurityEvent(
            contract_version=CONTRACT_VERSION,
            trace_id="t1",
            issued_at=datetime.now(UTC),
            kind="cli.subprocess.spawned",
            pool_id="p:1",
            agent_name="bot",
            skip_permissions=False,
            tools_restricted=False,
            tools_allowlist=[],
            model="claude-opus-4-6",
            pid=1,
        )

        with caplog.at_level(logging.WARNING):
            await sink.emit(event)  # must not raise

        assert any("AUDIT" in r.message for r in caplog.records)

    async def test_emit_degraded_logs_to_security_logger(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """In degraded mode emit() writes JSON to lyra.security at WARNING."""
        from datetime import UTC, datetime

        from lyra.infrastructure.audit.jetstream_sink import JetStreamAuditSink
        from roxabi_contracts.audit import SecurityEvent
        from roxabi_contracts.envelope import CONTRACT_VERSION

        sink = JetStreamAuditSink()
        sink._degraded = True  # type: ignore[attr-defined]

        event = SecurityEvent(
            contract_version=CONTRACT_VERSION,
            trace_id="t2",
            issued_at=datetime.now(UTC),
            kind="cli.subprocess.spawned",
            pool_id="p:2",
            agent_name="bot2",
            skip_permissions=True,
            tools_restricted=False,
            tools_allowlist=[],
            model="claude-opus-4-6",
            pid=2,
        )

        with caplog.at_level(logging.WARNING, logger="lyra.security"):
            await sink.emit(event)

        security_records = [r for r in caplog.records if r.name == "lyra.security"]
        assert len(security_records) == 1
        import json
        payload = json.loads(security_records[0].getMessage())
        assert payload["pid"] == 2
        assert payload["skip_permissions"] is True
