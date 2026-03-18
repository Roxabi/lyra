"""Shared test helpers for core tests."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.agent_store import AgentRow, AgentStore
from lyra.core.circuit_breaker import CircuitBreaker, CircuitRegistry
from lyra.core.command_loader import CommandLoader
from lyra.core.command_parser import CommandParser
from lyra.core.command_router import CommandRouter
from lyra.core.message import (
    InboundAudio,
    InboundMessage,
    OutboundMessage,
    Response,
)
from lyra.core.pool import Pool
from lyra.core.trust import TrustLevel


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
    """Minimal ChannelAdapter for testing."""

    def normalize(self, raw: object) -> InboundMessage:
        raise NotImplementedError

    def normalize_audio(
        self,
        raw: object,
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
        chunks: object,
        outbound: object = None,
    ) -> None:
        pass

    async def render_audio(self, msg: object, inbound: InboundMessage) -> None:
        pass

    async def render_audio_stream(
        self, chunks: object, inbound: InboundMessage
    ) -> None:
        pass

    async def render_voice_stream(
        self, chunks: object, inbound: InboundMessage
    ) -> None:
        pass

    async def render_attachment(self, msg: object, inbound: InboundMessage) -> None:
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
        command=cmd_ctx,  # type: ignore[call-arg]  # field added in #153
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


def make_inbound_message(
    platform: str = "telegram",
    bot_id: str = "main",
    user_id: str = "alice",
    scope_id: str | None = None,
    platform_meta: dict | None = None,
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
    return InboundMessage(
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
        persona=None,
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
