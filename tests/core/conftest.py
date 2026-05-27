"""Shared test helpers for core tests."""

from __future__ import annotations

import json as _json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.agent import Agent, AgentBase
from lyra.core.agent.agent_config import ModelConfig
from lyra.core.cli.cli_pool import _ProcessEntry
from lyra.core.hub import Hub
from lyra.core.messaging.message import (
    InboundMessage,
    OutboundMessage,
    Platform,
    Response,
    RoutingContext,
)
from lyra.core.pool import Pool

# Backward-compatible re-exports from domain factories
from tests.factories.agents import (
    FakeSTT,
    FastAgent,
    MockAdapter,
    RecordingAgent,
    SlowAgent,
)
from tests.factories.messages import (
    _PAIRING_ADMIN_ID,
    _PAIRING_USER_ID,
    make_debouncer_msg,
    make_dispatcher_msg,
    make_inbound_message,
    make_message,
    make_pairing_message,
    make_routing_inbound,
)
from tests.factories.plugins import make_echo_plugin_dir, make_plugin, make_router
from tests.factories.pools import _make_ctx_mock, ctx_mock, fast_pool, make_msg, pool
from tests.factories.stores import (
    _open_pairing_managers,
    _open_pairing_stores,
    agent_store,
    auth_store,
    bot_store,
    json_agent_store,
    make_agent_row,
    make_auth_store,
    make_circuit_registry,
    make_pairing_auth_store,
    make_pairing_pm,
    make_store,
)

__all__ = [
    "FakeSTT",
    "FastAgent",
    "MockAdapter",
    "RecordingAgent",
    "SlowAgent",
    "_PAIRING_ADMIN_ID",
    "_PAIRING_USER_ID",
    "make_debouncer_msg",
    "make_dispatcher_msg",
    "make_inbound_message",
    "make_message",
    "make_pairing_message",
    "make_routing_inbound",
    "make_echo_plugin_dir",
    "make_plugin",
    "make_router",
    "_make_ctx_mock",
    "ctx_mock",
    "fast_pool",
    "make_msg",
    "pool",
    "_open_pairing_managers",
    "_open_pairing_stores",
    "agent_store",
    "auth_store",
    "bot_store",
    "json_agent_store",
    "make_agent_row",
    "make_auth_store",
    "make_circuit_registry",
    "make_pairing_auth_store",
    "make_pairing_pm",
    "make_store",
]

# ---------------------------------------------------------------------------
# MessageManager shared constants
# ---------------------------------------------------------------------------

# Absolute path so tests run regardless of cwd
MESSAGES_TOML_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "lyra"
    / "data"
    / "messages.toml"
)


async def push_to_hub(hub: Hub, msg: InboundMessage) -> None:
    """Inject *msg* into the hub's inbound bus for testing.

    Registers the platform and starts the bus feeders if needed, then
    enqueues via the ``Bus`` Protocol's ``put()`` method.
    """
    from lyra.core.messaging.inbound_bus import LocalBus

    platform = Platform(msg.platform)
    bus = hub.inbound_bus
    if platform not in bus.registered_platforms():
        bus.register(platform)
    if isinstance(bus, LocalBus) and not bus._feeders:
        await bus.start()
    await bus.put(platform, msg)


# ---------------------------------------------------------------------------
# StreamingIterator shared helpers (used by test_cli_streaming_parse +
# test_cli_streaming_lifecycle)
# ---------------------------------------------------------------------------


def _ndjson(obj: dict) -> bytes:
    return (_json.dumps(obj) + "\n").encode()


def make_fake_proc(stdout_lines: list[bytes]) -> MagicMock:
    """Return a mock Process with controllable stdout readline side-effects."""
    proc = MagicMock()
    proc.returncode = None  # alive
    proc.pid = 99

    proc.stdin = MagicMock()
    proc.stdin.write = MagicMock()
    proc.stdin.drain = AsyncMock(return_value=None)

    lines_with_eof = list(stdout_lines) + [b""]
    proc.stdout = MagicMock()
    proc.stdout.readline = AsyncMock(side_effect=lines_with_eof)

    proc.stderr = None  # _read_stderr_snippet checks for None before reading
    proc.terminate = MagicMock()
    proc.wait = AsyncMock(return_value=0)
    proc.kill = MagicMock()

    return proc


def make_entry(proc: MagicMock, pool_id: str = "pool-test") -> _ProcessEntry:
    return _ProcessEntry(proc=proc, pool_id=pool_id, model_config=ModelConfig())


DEFAULT_POOL_ID = "pool-stream"

INIT_LINE = _ndjson({"type": "system", "subtype": "init", "session_id": "abc-123"})
TEXT_DELTA_LINE = _ndjson(
    {
        "type": "stream_event",
        "event": {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": "Hello"},
        },
    }
)
TEXT_DELTA_LINE2 = _ndjson(
    {
        "type": "stream_event",
        "event": {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": " world"},
        },
    }
)
INPUT_JSON_DELTA_LINE = _ndjson(
    {
        "type": "stream_event",
        "event": {
            "type": "content_block_delta",
            "delta": {"type": "input_json_delta", "partial_json": '{"key":'},
        },
    }
)
RESULT_LINE = _ndjson(
    {
        "type": "result",
        "session_id": "abc-123",
        "duration_ms": 100,
        "is_error": False,
    }
)
ERROR_RESULT_LINE = _ndjson(
    {
        "type": "result",
        "session_id": "abc-123",
        "result": "Something went wrong",
        "is_error": True,
        "subtype": "api_error",
        "duration_ms": 50,
    }
)
ASSISTANT_INTERMEDIATE_LINE = _ndjson(
    {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "I need to check something first."}],
        },
    }
)
ASSISTANT_INTERMEDIATE_LINE2 = _ndjson(
    {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "Let me verify that too."}],
        },
    }
)


# ---------------------------------------------------------------------------
# MessagePipeline shared helpers (used by test_message_pipeline_guards +
# test_message_pipeline_context)
# ---------------------------------------------------------------------------


class _MockAdapter(MockAdapter):
    """Adapter that records sent messages — extends MockAdapter."""

    def __init__(self) -> None:
        self.sent: list[OutboundMessage] = []

    async def send(
        self,
        original_msg: InboundMessage,
        outbound: OutboundMessage,
    ) -> None:
        self.sent.append(outbound)


class _NullAgent(AgentBase):
    """Minimal agent for testing — returns a fixed response."""

    async def process(
        self,
        msg: InboundMessage,
        pool: Pool,  # noqa: F811
        *,
        on_intermediate=None,
    ) -> Response:
        return Response(content="ok")


def _make_hub(**kwargs: Any) -> Hub:
    """Build a Hub with an agent, adapter, and binding pre-wired."""
    hub = Hub(**kwargs)

    agent = _NullAgent(
        Agent(
            name="lyra",
            system_prompt="",
            memory_namespace="lyra",
        )
    )
    hub.register_agent(agent)

    adapter = _MockAdapter()
    hub.register_adapter(Platform.TELEGRAM, "main", adapter)
    hub.register_binding(
        Platform.TELEGRAM,
        "main",
        "*",
        "lyra",
        "telegram:main:*",
    )
    return hub


# ---------------------------------------------------------------------------
# RoutingContext shared constants and helpers
# (used by test_routing_context_basics + test_routing_context_integration)
# ---------------------------------------------------------------------------

_RC_TG = RoutingContext(platform="telegram", bot_id="main", scope_id="chat:123")
_RC_DC = RoutingContext(platform="discord", bot_id="main", scope_id="channel:456")


# ---------------------------------------------------------------------------
# Pairing shared helpers (used by test_pairing_core + test_pairing_commands)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def _cleanup_pairing_state(tmp_path: Path):
    """Reset pairing global and close all PairingManagers/AuthStores after each test."""
    from lyra.infrastructure.stores.pairing import set_pairing_manager

    setattr(_cleanup_pairing_state, "tmp_path", tmp_path)
    yield
    set_pairing_manager(None)
    for pm in _open_pairing_managers:
        await pm.close()
    _open_pairing_managers.clear()
    for store in _open_pairing_stores:
        await store.close()
    _open_pairing_stores.clear()
