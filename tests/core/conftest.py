"""Shared test helpers for core tests."""

from __future__ import annotations

import asyncio
import json as _json
from collections.abc import AsyncIterator
from dataclasses import dataclass as _dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.agent import Agent, AgentBase
from lyra.core.agent_config import ModelConfig
from lyra.core.circuit_breaker import CircuitBreaker, CircuitRegistry
from lyra.core.cli_pool import _ProcessEntry
from lyra.core.commands.command_loader import CommandLoader
from lyra.core.commands.command_parser import CommandParser
from lyra.core.commands.command_router import CommandRouter
from lyra.core.hub import Hub
from lyra.core.message import (
    Attachment,
    InboundAudio,
    InboundMessage,
    OutboundAttachment,
    OutboundAudio,
    OutboundAudioChunk,
    OutboundMessage,
    Platform,
    Response,
    RoutingContext,
)
from lyra.core.pool import Pool
from lyra.core.render_events import RenderEvent
from lyra.core.stores.agent_store import AgentRow, AgentStore
from lyra.core.stores.auth_store import AuthStore
from lyra.core.stores.pairing import PairingConfig, PairingManager
from lyra.core.trust import TrustLevel

# ---------------------------------------------------------------------------
# MessageManager shared constants
# ---------------------------------------------------------------------------

# Absolute path so tests run regardless of cwd
MESSAGES_TOML_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "lyra"
    / "config"
    / "messages.toml"
)


def make_plugin(
    tmp_path: Path,
    name: str,
    handler_name: str = "cmd_fn",
    cmd_name: str = "cmd",
) -> Path:
    """Create a minimal valid plugin directory under tmp_path/name/.

    Writes:
    - plugin.toml  — minimal manifest referencing *handler_name* for *cmd_name*
    - handlers.py  — async function *handler_name* that returns 'ok'
    """
    plugin_dir = tmp_path / name
    plugin_dir.mkdir(exist_ok=True)
    (plugin_dir / "plugin.toml").write_text(
        f'name = "{name}"\n'
        f"[[commands]]\n"
        f'name = "{cmd_name}"\n'
        f'description = "test"\n'
        f'handler = "{handler_name}"\n'
    )
    (plugin_dir / "handlers.py").write_text(
        f"async def {handler_name}(msg, pool, args): return 'ok'\n"
    )
    return plugin_dir


class MockAdapter:
    """Typed ChannelAdapter test double — implements the full protocol."""

    def normalize(self, raw: Any) -> InboundMessage:
        raise NotImplementedError

    def normalize_audio(
        self,
        raw: Any,
        audio_bytes: bytes,
        mime_type: str,
        *,
        trust_level: TrustLevel,
    ) -> InboundAudio:
        raise NotImplementedError

    async def send(
        self, original_msg: InboundMessage, outbound: OutboundMessage
    ) -> None:
        pass

    async def send_streaming(
        self,
        original_msg: InboundMessage,
        events: AsyncIterator[RenderEvent],
        outbound: OutboundMessage | None = None,
    ) -> None:
        pass

    async def render_audio(
        self, msg: OutboundAudio, inbound: InboundMessage
    ) -> None:
        pass

    async def render_audio_stream(
        self, chunks: AsyncIterator[OutboundAudioChunk], inbound: InboundMessage
    ) -> None:
        pass

    async def render_voice_stream(
        self, chunks: AsyncIterator[OutboundAudioChunk], inbound: InboundMessage
    ) -> None:
        pass

    async def render_attachment(
        self, msg: OutboundAttachment, inbound: InboundMessage
    ) -> None:
        pass


def make_circuit_registry(**overrides) -> CircuitRegistry:
    """Build a CircuitRegistry with default CBs for all 4 services."""
    registry = CircuitRegistry()
    defaults = {
        "anthropic": CircuitBreaker(
            "anthropic", failure_threshold=3, recovery_timeout=60
        ),
        "telegram": CircuitBreaker(
            "telegram", failure_threshold=5, recovery_timeout=30
        ),
        "discord": CircuitBreaker("discord", failure_threshold=5, recovery_timeout=30),
        "hub": CircuitBreaker("hub", failure_threshold=10, recovery_timeout=60),
    }
    for name, cb in defaults.items():
        if name in overrides:
            registry.register(overrides[name])
        else:
            registry.register(cb)
    return registry


def make_message(
    content: str = "hello",
    platform: str = "telegram",
    bot_id: str = "main",
    user_id: str = "alice",
    *,
    is_admin: bool = False,
) -> InboundMessage:
    """Build a minimal InboundMessage for command router tests.

    Auto-parses CommandContext and attaches it to the message, mirroring
    what the Hub pipeline does.
    """
    _parser = CommandParser()
    cmd_ctx = _parser.parse(content)
    return InboundMessage(
        id="msg-test-1",
        platform=platform,
        bot_id=bot_id,
        scope_id="chat:42",
        user_id=user_id,
        user_name="Alice",
        is_mention=False,
        text=content,
        text_raw=content,
        timestamp=datetime.now(timezone.utc),
        platform_meta={
            "chat_id": 42,
            "topic_id": None,
            "message_id": None,
            "is_group": False,
        },
        trust_level=TrustLevel.TRUSTED,
        is_admin=is_admin,
        command=cmd_ctx,
    )


def make_echo_plugin_dir(tmpdir: Path) -> Path:
    """Create a minimal echo plugin in tmpdir/echo/."""
    plugin_dir = tmpdir / "echo"
    plugin_dir.mkdir(exist_ok=True)
    (plugin_dir / "plugin.toml").write_text(
        'name = "echo"\n'
        'description = "Echo back"\n'
        "[[commands]]\n"
        'name = "echo"\n'
        'description = "Echo back the message (test command)"\n'
        'handler = "cmd_echo"\n'
    )
    (plugin_dir / "handlers.py").write_text(
        "from lyra.core.message import Response, InboundMessage\n"
        "from lyra.core.pool import Pool\n"
        "async def cmd_echo(\n"
        "    msg: InboundMessage, pool: Pool, args: list[str]\n"
        ") -> Response:\n"
        '    return Response(content=" ".join(args))\n'
    )
    return tmpdir


def make_router(
    tmp_path: Path,
    enabled: list[str] | None = None,
    patterns: dict | None = None,
) -> CommandRouter:
    """Build a CommandRouter with the echo plugin loaded."""
    plugins_dir = make_echo_plugin_dir(tmp_path)
    loader = CommandLoader(plugins_dir)
    loader.load("echo")
    effective = enabled if enabled is not None else ["echo"]
    _patterns = patterns if patterns is not None else {"bare_url": True}
    return CommandRouter(
        command_loader=loader, enabled_plugins=effective, patterns=_patterns
    )


def make_inbound_message(  # noqa: PLR0913
    platform: str = "telegram",
    bot_id: str = "main",
    user_id: str = "alice",
    scope_id: str | None = None,
    platform_meta: dict | None = None,
    modality: str | None = None,
) -> InboundMessage:
    """Build a minimal InboundMessage for hub tests."""
    if platform == "telegram":
        _scope = scope_id if scope_id is not None else "chat:42"
        _meta = (
            platform_meta
            if platform_meta is not None
            else {
                "chat_id": 42,
                "topic_id": None,
                "message_id": None,
                "is_group": False,
            }
        )
    elif platform == "discord":
        _scope = scope_id if scope_id is not None else "channel:333"
        _meta = (
            platform_meta
            if platform_meta is not None
            else {
                "guild_id": 111,
                "channel_id": 333,
                "message_id": 555,
                "thread_id": None,
                "channel_type": "text",
            }
        )
    else:
        _scope = scope_id if scope_id is not None else f"{platform}:default"
        _meta = platform_meta or {}
    kwargs: dict = dict(
        id="msg-1",
        platform=platform,
        bot_id=bot_id,
        scope_id=_scope,
        user_id=user_id,
        user_name="Alice",
        is_mention=False,
        text="hello",
        text_raw="hello",
        timestamp=datetime.now(timezone.utc),
        platform_meta=_meta,
        trust_level=TrustLevel.TRUSTED,
    )
    if modality is not None:
        kwargs["modality"] = modality
    return InboundMessage(**kwargs)


async def push_to_hub(hub: Hub, msg: InboundMessage) -> None:
    """Inject *msg* into the hub's inbound bus for testing.

    Registers the platform and starts the bus feeders if needed, then
    enqueues via the ``Bus`` Protocol's ``put()`` method.
    """
    from lyra.core.inbound_bus import LocalBus

    platform = Platform(msg.platform)
    bus = hub.inbound_bus
    if platform not in bus.registered_platforms():
        bus.register(platform)
    if isinstance(bus, LocalBus) and not bus._feeders:
        await bus.start()
    bus.put(platform, msg)


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
# AuthStore shared helpers (used by test_auth_store_connect, test_auth_store_check,
# test_auth_store_upsert_seed)
# ---------------------------------------------------------------------------


async def make_auth_store(tmp_path: Path) -> AuthStore:
    """Create and connect a real AuthStore backed by a tmp file DB.

    Prefer the ``auth_store`` pytest fixture for new tests — it provides
    automatic teardown via ``yield`` + ``await store.close()``.
    """
    store = AuthStore(db_path=str(tmp_path / "grants.db"))
    await store.connect()
    return store


@pytest.fixture
async def auth_store(tmp_path: Path):
    """Fixture-based AuthStore with automatic teardown. Prefer over make_auth_store."""
    store = await make_auth_store(tmp_path)
    try:
        yield store
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# AgentStore shared helpers (used by test_agent_store_crud + test_agent_store_seed)
# ---------------------------------------------------------------------------


def make_agent_row(name: str = "test-agent") -> AgentRow:
    """Return a minimal valid AgentRow for the given name."""
    return AgentRow(
        name=name,
        backend="anthropic-sdk",
        model="claude-3-5-haiku-20241022",
        max_turns=10,
        tools_json="[]",
        show_intermediate=False,
        smart_routing_json=None,
        plugins_json="[]",
        memory_namespace=None,
        cwd=None,
        source="test",
    )


async def make_store(tmp_path: Path) -> AgentStore:
    """Create and connect a real AgentStore backed by a tmp file DB."""
    store = AgentStore(db_path=str(tmp_path / "agents.db"))
    await store.connect()
    return store


@pytest.fixture
async def agent_store(tmp_path: Path):
    """Fixture-based AgentStore with automatic teardown."""
    store = await make_store(tmp_path)
    try:
        yield store
    finally:
        await store.close()


@pytest.fixture
async def json_agent_store(tmp_path: Path):
    """JsonAgentStore fixture backed by a tmp JSON file — no SQLite needed.

    Use this in tests that exercise agent configuration logic but do not
    specifically test the SQLite implementation.  Faster and DB-free.
    """
    from lyra.core.stores.json_agent_store import JsonAgentStore

    store = JsonAgentStore(path=tmp_path / "agents_test.json")
    await store.connect()
    try:
        yield store
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# OutboundDispatcher shared helpers (used by test_outbound_dispatcher_queue,
# test_outbound_dispatcher_media)
# ---------------------------------------------------------------------------


def make_dispatcher_msg() -> InboundMessage:
    """Build a minimal InboundMessage for OutboundDispatcher tests."""
    return InboundMessage(
        id="msg-1",
        platform="telegram",
        bot_id="main",
        scope_id="chat:123",
        user_id="tg:user:42",
        user_name="Alice",
        is_mention=False,
        text="hello",
        text_raw="hello",
        timestamp=datetime.now(timezone.utc),
        platform_meta={
            "chat_id": 123,
            "topic_id": None,
            "message_id": None,
            "is_group": False,
        },
        trust_level=TrustLevel.TRUSTED,
    )


# ---------------------------------------------------------------------------
# Debouncer shared helpers (used by test_debouncer_merge, test_debouncer_collect,
# test_debouncer_pool, test_debouncer_runtime_config)
# ---------------------------------------------------------------------------


def make_debouncer_msg(
    text: str = "hello",
    msg_id: str = "msg-1",
    is_mention: bool = False,
    attachments: list[Attachment] | None = None,
) -> InboundMessage:
    """Build a minimal InboundMessage for debouncer tests.

    Supports msg_id, is_mention, attachments.
    """
    return InboundMessage(
        id=msg_id,
        platform="telegram",
        bot_id="main",
        scope_id="chat:1",
        user_id="tg:user:1",
        user_name="Alice",
        is_mention=is_mention,
        text=text,
        text_raw=text,
        timestamp=datetime.now(timezone.utc),
        platform_meta={
            "chat_id": 1,
            "topic_id": None,
            "message_id": None,
            "is_group": False,
        },
        trust_level=TrustLevel.TRUSTED,
        attachments=attachments or [],
    )


class RecordingAgent:
    """Agent that records the text it receives."""

    name = "test_agent"

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def process(
        self, msg: InboundMessage, pool: Pool, *, on_intermediate=None
    ) -> Response:
        self.calls.append(msg.text)
        return Response(content=f"reply:{msg.text}")


# ---------------------------------------------------------------------------
# Pool shared helpers (used by test_pool_tasks, test_pool_streaming,
# test_pool_advanced)
# ---------------------------------------------------------------------------


def _make_ctx_mock(agents: dict | None = None) -> MagicMock:
    """Build a minimal PoolContext mock."""
    ctx = MagicMock()
    _agents: dict = agents or {}
    ctx.get_agent = MagicMock(side_effect=lambda name: _agents.get(name))
    ctx.get_message = MagicMock(return_value=None)
    ctx.dispatch_response = AsyncMock(return_value=None)
    ctx.dispatch_streaming = AsyncMock(return_value=None)
    ctx.record_circuit_success = MagicMock()
    ctx.record_circuit_failure = MagicMock()
    # Keep a reference so tests can mutate the agent registry
    ctx._agents = _agents
    return ctx


@pytest.fixture
def ctx_mock() -> MagicMock:
    """Minimal PoolContext stub with the methods Pool._process_loop() touches."""
    return _make_ctx_mock()


@pytest.fixture
def pool(ctx_mock: MagicMock) -> Pool:
    """Pool with a very long timeout (not triggered in normal tests)."""
    return Pool(
        pool_id="test:main:chat:1",
        agent_name="test_agent",
        ctx=ctx_mock,
        turn_timeout=60.0,
        debounce_ms=0,
    )


@pytest.fixture
def fast_pool(ctx_mock: MagicMock) -> Pool:
    """Pool with a very short timeout for timeout tests."""
    return Pool(
        pool_id="test:main:chat:1",
        agent_name="test_agent",
        ctx=ctx_mock,
        turn_timeout=0.05,
        debounce_ms=0,
    )


def make_msg(text: str = "hello") -> InboundMessage:
    """Build a minimal InboundMessage for pool tests."""
    return InboundMessage(
        id="msg-1",
        platform="telegram",
        bot_id="main",
        scope_id="chat:1",
        user_id="tg:user:1",
        user_name="Alice",
        is_mention=False,
        text=text,
        text_raw=text,
        timestamp=datetime.now(timezone.utc),
        platform_meta={
            "chat_id": 1,
            "topic_id": None,
            "message_id": None,
            "is_group": False,
        },
        trust_level=TrustLevel.TRUSTED,
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
        pool: Pool,
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


async def _drain(pool: Pool, *, timeout: float = 2.0) -> None:
    """Yield to the event loop then wait for the current task to finish."""
    await asyncio.sleep(0)
    if pool._current_task is not None:
        await asyncio.wait_for(pool._current_task, timeout=timeout)


class SlowAgent:
    """Agent whose process() never returns within test timeouts."""

    name = "test_agent"

    def is_backend_alive(self, pool_id: str) -> bool:
        return True

    async def reset_backend(self, pool_id: str) -> None:
        pass

    async def process(
        self,
        msg: InboundMessage,
        pool: Pool,
        *,
        on_intermediate=None,
    ) -> Response:
        await asyncio.sleep(10)  # never finishes in test
        return Response(content="done")


class FastAgent:
    """Agent that echoes the message text immediately."""

    name = "test_agent"

    async def process(
        self,
        msg: InboundMessage,
        pool: Pool,
        *,
        on_intermediate=None,
    ) -> Response:
        return Response(content=f"echo: {msg.text}")


# ---------------------------------------------------------------------------
# AudioPipeline shared helpers (used by test_audio_pipeline_constraints +
# test_audio_pipeline_tts)
# ---------------------------------------------------------------------------


def make_audio(
    audio_id: str = "audio-1",
    platform: str = "telegram",
    trust_level: TrustLevel = TrustLevel.TRUSTED,
    user_id: str = "alice",
) -> InboundAudio:
    """Build a minimal InboundAudio for audio pipeline tests."""
    from datetime import datetime, timezone

    return InboundAudio(
        id=audio_id,
        platform=platform,
        bot_id="main",
        scope_id="chat:42",
        user_id=user_id,
        audio_bytes=b"\x00" * 100,
        mime_type="audio/ogg",
        duration_ms=3000,
        file_id="file-1",
        timestamp=datetime.now(timezone.utc),
        trust_level=trust_level,
        user_name="Alice",
        platform_meta={"chat_id": 42, "is_group": False},
    )


@_dataclass
class FakeTranscription:
    text: str
    language: str = "en"
    duration_seconds: float = 2.5


class FakeSTT:
    def __init__(self, text: str = "Hello world") -> None:
        self._text = text

    async def transcribe(self, path):
        return FakeTranscription(text=self._text)


# ---------------------------------------------------------------------------
# RoutingContext shared constants and helpers
# (used by test_routing_context_basics + test_routing_context_integration)
# ---------------------------------------------------------------------------

_RC_TG = RoutingContext(platform="telegram", bot_id="main", scope_id="chat:123")
_RC_DC = RoutingContext(platform="discord", bot_id="main", scope_id="channel:456")


def make_routing_inbound(routing: RoutingContext | None = None) -> InboundMessage:
    """Build a minimal InboundMessage for routing context tests."""
    return InboundMessage(
        id="msg-1",
        platform="telegram",
        bot_id="main",
        scope_id="chat:123",
        user_id="tg:user:42",
        user_name="Alice",
        is_mention=False,
        text="hello",
        text_raw="hello",
        timestamp=datetime.now(timezone.utc),
        trust_level=TrustLevel.TRUSTED,
        platform_meta={"chat_id": 123},
        routing=routing,
    )


# ---------------------------------------------------------------------------
# Pairing shared helpers (used by test_pairing_core + test_pairing_commands)
# ---------------------------------------------------------------------------

_PAIRING_ADMIN_ID = "admin-user-1"
_PAIRING_USER_ID = "regular-user-1"

# Track PairingManagers and AuthStores created in tests for cleanup
_open_pairing_managers: list[PairingManager] = []
_open_pairing_stores: list[AuthStore] = []


@pytest.fixture(autouse=True)
async def _cleanup_pairing_state(tmp_path: Path):
    """Reset pairing global and close all PairingManagers/AuthStores after each test."""
    from lyra.core.stores.pairing import set_pairing_manager

    setattr(_cleanup_pairing_state, "tmp_path", tmp_path)
    yield
    set_pairing_manager(None)
    for pm in _open_pairing_managers:
        await pm.close()
    _open_pairing_managers.clear()
    for store in _open_pairing_stores:
        await store.close()
    _open_pairing_stores.clear()


def make_pairing_message(  # noqa: PLR0913 — test factory with optional overrides
    content: str = "hello",
    platform: Platform = Platform.TELEGRAM,
    bot_id: str = "main",
    user_id: str = _PAIRING_USER_ID,
    is_group: bool = False,
    guild_id: int | None = None,
    *,
    is_admin: bool = False,
) -> InboundMessage:
    """Build a minimal InboundMessage for pairing tests."""
    if platform == Platform.DISCORD:
        scope = "channel:1"
        meta = {
            "guild_id": guild_id,
            "channel_id": 1,
            "message_id": 1,
            "thread_id": None,
            "channel_type": "text",
        }
    else:
        scope = "chat:42"
        meta = {
            "chat_id": 42,
            "topic_id": None,
            "message_id": None,
            "is_group": is_group,
        }

    return InboundMessage(
        id="msg-test-1",
        platform=platform.value,
        bot_id=bot_id,
        scope_id=scope,
        user_id=user_id,
        user_name="Tester",
        is_mention=False,
        text=content,
        text_raw=content,
        timestamp=datetime.now(timezone.utc),
        platform_meta=meta,
        trust_level=TrustLevel.TRUSTED,
        is_admin=is_admin,
    )


async def make_pairing_auth_store(db_path: str = ":memory:") -> AuthStore:
    """Build and connect a real AuthStore for pairing tests."""
    store = AuthStore(db_path=db_path)
    await store.connect()
    _open_pairing_stores.append(store)
    return store


async def make_pairing_pm(  # noqa: PLR0913 — test factory with optional overrides
    enabled: bool = True,
    max_pending: int = 3,
    rate_limit_attempts: int = 5,
    rate_limit_window: int = 300,
    session_max_age_days: int = 30,
    ttl_seconds: int = 3600,
    auth_store: AuthStore | None = None,
) -> PairingManager:
    """Build and connect a PairingManager backed by an in-memory SQLite DB."""
    if auth_store is None:
        auth_store = await make_pairing_auth_store()

    config = PairingConfig(
        enabled=enabled,
        max_pending=max_pending,
        rate_limit_attempts=rate_limit_attempts,
        rate_limit_window=rate_limit_window,
        session_max_age_days=session_max_age_days,
        ttl_seconds=ttl_seconds,
    )
    pm = PairingManager(
        config=config,
        db_path=":memory:",
        auth_store=auth_store,
    )
    await pm.connect()
    _open_pairing_managers.append(pm)
    return pm
