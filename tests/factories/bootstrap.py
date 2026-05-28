"""Bootstrap DI factories for tests — pure object creation, no global patching."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.hub import Hub
from roxabi_nats import _version_check as _vc_mod

__all__ = [
    "_FakeDcAdapter",
    "_FakeDp",
    "_FakeTgAdapter",
    "_reset_version_check_log_state",
    "make_fake_auth_store",
    "make_fake_agent_store",
    "make_fake_bot_store",
    "make_fake_tg_adapter",
    "make_fake_dc_adapter",
    "make_fake_nats_client",
    "make_fake_nats_bus",
    "make_fake_audit_sink",
    "make_fake_capturing_hub",
    "make_fake_credentials",
    "make_fake_lifecycle_resources",
    "make_fake_wired_adapters",
    "make_fake_hub",
    "make_fake_tg_adapter_mock",
    "make_fake_agent_row",
    "make_fake_auth_middleware",
]


@pytest.fixture(autouse=True)
def _reset_version_check_log_state() -> None:
    """Clear the module-level log rate-limit state before every test.

    ``roxabi_nats._version_check`` holds a process-wide dict of last-log
    timestamps so repeat drops within 60 s are silent.  Tests that assert on
    ``log.error`` firing would be flaky across test ordering without this
    reset, since one test's logged drop would silence another's.
    """
    _vc_mod._reset_log_state()


class _FakeDp:
    """Fake Dispatcher that blocks until explicitly stopped.

    Uses an event that never fires (unless test sets it) instead of arbitrary sleep.
    """

    def __init__(self, shutdown_event: asyncio.Event | None = None) -> None:
        self._shutdown = shutdown_event if shutdown_event else asyncio.Event()

    async def start_polling(self, bot: object, **kwargs: object) -> None:
        await self._shutdown.wait()  # explicit: wait for teardown signal


class _FakeTgAdapter:
    bot = MagicMock()

    def __init__(
        self, shutdown_event: asyncio.Event | None = None, **kwargs: object
    ) -> None:
        self._bot_id = kwargs.get("bot_id", "main")
        self.dp = _FakeDp(shutdown_event)

    async def send(self, msg: object, response: object) -> None:
        pass

    async def resolve_identity(self) -> None:
        pass


class _FakeDcAdapter:
    """Fake Discord adapter that blocks until explicitly stopped.

    Uses an event that never fires (unless test sets it) instead of arbitrary sleep.
    """

    def __init__(
        self, shutdown_event: asyncio.Event | None = None, **kwargs: object
    ) -> None:
        self._shutdown = shutdown_event if shutdown_event else asyncio.Event()

    async def start(self, token: str) -> None:
        await self._shutdown.wait()  # explicit: wait for teardown signal

    async def close(self) -> None:
        self._shutdown.set()  # signal stop if still waiting

    async def send(self, msg: object, response: object) -> None:
        pass


# ---------------------------------------------------------------------------
# DI factory functions — construct and return fakes, no global patching
# ---------------------------------------------------------------------------


def make_fake_auth_store() -> MagicMock:
    """Return a fake AuthStore with async connect/seed/close."""
    fake_auth_store = MagicMock()
    fake_auth_store.connect = AsyncMock()
    fake_auth_store.seed_from_config = AsyncMock()
    fake_auth_store.close = AsyncMock()
    return fake_auth_store


def make_fake_agent_store(agent_name: str = "lyra_default") -> MagicMock:
    """Return a fake AgentStore with async connect/close and get/get_bot_agent."""
    _fake_agent_row = MagicMock()
    _fake_agent_row.name = agent_name
    fake_agent_store = MagicMock()
    fake_agent_store.connect = AsyncMock()
    fake_agent_store.close = AsyncMock()
    fake_agent_store.get_bot_agent = MagicMock(return_value=None)
    fake_agent_store.get = MagicMock(return_value=_fake_agent_row)
    fake_agent_store.set_bot_agent = AsyncMock()
    return fake_agent_store


def make_fake_bot_store() -> MagicMock:
    """Return a fake BotStore with pre-filled BotRow entries."""
    from lyra.core.agent.bot_models import BotRow

    fake_bot_store = MagicMock()
    fake_bot_store.connect = AsyncMock()
    fake_bot_store.close = AsyncMock()
    fake_bot_store.get_all = MagicMock(
        return_value=[
            BotRow(platform="telegram", bot_id="main", agent="lyra_default"),
            BotRow(platform="discord", bot_id="main", agent="lyra_default"),
        ]
    )
    return fake_bot_store


def make_fake_credentials(
    *,
    tg_creds: tuple[str, str | None] | None = ("fake-token", "fake-secret"),
    dc_creds: tuple[str, str | None] | None = ("fake-dc-token", None),
) -> Any:
    """Return a fake ``load_bot_token`` callable.

    Use with ``patch("lyra.bootstrap.credentials.load_bot_token",
    new=make_fake_credentials())``.
    """

    def _fake_load(platform: str, bot_id: str) -> tuple[str, str | None]:
        if platform == "telegram":
            return tg_creds or ("fake-token", None)
        if platform == "discord":
            return dc_creds or ("fake-dc-token", None)
        return ("fake-token", None)

    return _fake_load


def make_fake_tg_adapter(**kwargs: Any) -> _FakeTgAdapter:
    """Return a real _FakeTgAdapter instance."""
    return _FakeTgAdapter(**kwargs)


def make_fake_tg_adapter_mock() -> MagicMock:
    """Return a MagicMock suitable for patching TelegramAdapter in wiring tests."""
    mock = MagicMock()
    mock.resolve_identity = AsyncMock()
    mock._outbound_listener = None
    return mock


def make_fake_dc_adapter(**kwargs: Any) -> _FakeDcAdapter:
    """Return a real _FakeDcAdapter instance."""
    return _FakeDcAdapter(**kwargs)


def make_fake_nats_client() -> AsyncMock:
    """Return a fake NATS client with async close."""
    fake_nc = AsyncMock()
    fake_nc.close = AsyncMock()
    return fake_nc


def make_fake_nats_bus() -> MagicMock:
    """Return a fake NatsBus with async start/stop."""
    fake_nats_bus = MagicMock()
    fake_nats_bus.start = AsyncMock()
    fake_nats_bus.stop = AsyncMock()
    return fake_nats_bus


def make_fake_audit_sink() -> MagicMock:
    """Return a fake JetStreamAuditSink with async provision/emit."""
    fake_audit_sink = MagicMock()
    fake_audit_sink.provision = AsyncMock()
    fake_audit_sink.emit = AsyncMock()
    return fake_audit_sink


def make_fake_capturing_hub() -> tuple[type[Hub], list[Hub]]:
    """Return a Hub subclass that captures every instantiated Hub.

    Usage::

        CapturingHub, captured = make_fake_capturing_hub()
        with patch("lyra.bootstrap.factory.wiring_helpers.Hub", CapturingHub):
            ...
    """
    captured: list[Hub] = []
    _OriginalHub = Hub

    class CapturingHub(_OriginalHub):  # type: ignore[misc]
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            captured.append(self)

    return CapturingHub, captured


def make_fake_auth_middleware() -> tuple[MagicMock, MagicMock]:
    """Return (mock_tg_auth, mock_dc_auth) for Authenticator.from_config."""
    return MagicMock(), MagicMock()


def make_fake_agent_row(name: str = "lyra_default") -> MagicMock:
    """Return a MagicMock with a ``name`` attribute."""
    row = MagicMock()
    row.name = name
    return row


def make_fake_hub() -> MagicMock:
    """Return a MagicMock configured like a Hub for lifecycle tests."""
    hub = MagicMock()
    hub.run = AsyncMock()
    hub.shutdown = AsyncMock()
    hub.notify_shutdown_inflight = AsyncMock()
    hub._event_bus = None
    hub._turn_store = MagicMock()
    hub.inbound_bus = MagicMock()
    hub.inbound_bus.start = AsyncMock()
    hub.inbound_bus.stop = AsyncMock()
    return hub


def make_fake_wired_adapters(
    dc_thread_store: Any = None,
) -> MagicMock:
    """Return a MagicMock shaped like WiredAdapters for lifecycle tests."""
    wired = MagicMock()
    wired.tg_adapters = []
    wired.tg_dispatchers = []
    wired.dc_adapters = []
    wired.dc_dispatchers = []
    wired.dc_thread_store = dc_thread_store
    return wired


def make_fake_lifecycle_resources() -> Any:
    """Return a LifecycleResources with empty fields."""
    from lyra.bootstrap.types import LifecycleResources

    return LifecycleResources(pm=None, cli_pool=None, nc=None)
