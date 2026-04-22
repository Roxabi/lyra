"""Shared pytest fixtures for the Lyra test suite."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import lyra.__main__ as main_mod
import lyra.bootstrap.bootstrap_stores as stores_mod
import lyra.bootstrap.factory.unified as unified_mod
import lyra.bootstrap.wiring.bootstrap_wiring as wiring_mod
from lyra.core.agent import Agent
from lyra.core.agent.agent_config import ModelConfig
from lyra.core.auth.authenticator import Authenticator as AuthMiddleware
from lyra.core.circuit_breaker import CircuitBreaker, CircuitRegistry
from lyra.core.hub import Hub
from lyra.core.pool import Pool
from roxabi_nats import _version_check as _vc_mod


@pytest.fixture(autouse=True)
def _reset_version_check_log_state() -> None:
    """Clear the module-level log rate-limit state before every test.

    ``roxabi_nats._version_check`` holds a process-wide dict of last-log
    timestamps so repeat drops within 60 s are silent.  Tests that assert on
    ``log.error`` firing would be flaky across test ordering without this
    reset, since one test's logged drop would silence another's.
    """
    _vc_mod._reset_log_state()


# ---------------------------------------------------------------------------
# Health endpoint shared constants
# ---------------------------------------------------------------------------

HEALTH_SECRET = "test-health-secret"

# Timeout constants for event-based coordination
TIMEOUT_FAST = 0.5  # In-memory operations
TIMEOUT_IO = 2.0  # Single network round-trip
TIMEOUT_SLOW = 5.0  # Multi-step coordination, CI variance buffer


async def yield_once() -> None:
    """Yield control to the event loop once. Replaces asyncio.sleep(0)."""
    await asyncio.sleep(0)


async def _drain(pool: Pool, *, timeout: float = TIMEOUT_IO) -> None:
    """Yield to the event loop then wait for the current task to finish."""
    await yield_once()
    if pool._current_task is not None:
        await asyncio.wait_for(pool._current_task, timeout=timeout)


AUTH_HEADERS = {"authorization": f"Bearer {HEALTH_SECRET}"}


# ---------------------------------------------------------------------------
# Health endpoint shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def circuit_registry() -> CircuitRegistry:
    registry = CircuitRegistry()
    for name in ("anthropic", "telegram", "discord", "hub"):
        registry.register(CircuitBreaker(name=name))
    return registry


@pytest.fixture()
def hub(circuit_registry: CircuitRegistry) -> Hub:
    return Hub(circuit_registry=circuit_registry)


def pytest_configure(config: pytest.Config) -> None:
    """Pre-flight: ensure .venv is synced before collecting tests.

    In fresh worktrees the venv may exist but be incomplete (e.g. concurrent
    ``uv run`` created it without finishing the install).  Running ``uv sync``
    once at session start is a fast no-op when everything is current and
    prevents flaky hangs from competing installs.
    """
    venv = Path(".venv")
    if not venv.exists() or not (venv / "bin" / "python").exists():
        subprocess.run(
            [sys.executable, "-m", "uv", "sync"],
            check=True,
            capture_output=True,
        )


# ---------------------------------------------------------------------------
# Shared fake adapters for bootstrap credential tests
# ---------------------------------------------------------------------------


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


def _patch_nats_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch NATS components so _bootstrap_unified never touches a real server."""
    fake_nc = AsyncMock()
    fake_nc.close = AsyncMock()
    fake_embedded = MagicMock()
    fake_embedded.stop = AsyncMock()
    monkeypatch.setattr(
        unified_mod,
        "ensure_nats",
        AsyncMock(return_value=(fake_nc, fake_embedded, "nats://localhost:4222")),
    )
    monkeypatch.setattr(unified_mod, "acquire_lockfile", lambda: None)
    monkeypatch.setattr(unified_mod, "release_lockfile", lambda: None)
    fake_nats_bus = MagicMock()
    fake_nats_bus.start = AsyncMock()
    fake_nats_bus.stop = AsyncMock()
    monkeypatch.setattr(unified_mod, "NatsBus", lambda **kw: fake_nats_bus)
    monkeypatch.setenv("NATS_URL", "nats://localhost:4222")
    monkeypatch.setenv("LYRA_HEALTH_PORT", "0")


def make_fake_stores(
    monkeypatch: pytest.MonkeyPatch,
    *,
    tg_creds: tuple[str, str | None] | None = ("fake-token", "fake-secret"),
    dc_creds: tuple[str, str | None] | None = ("fake-dc-token", None),
) -> tuple[MagicMock, MagicMock]:
    """Patch LyraKeyring and CredentialStore in stores_mod.

    Returns (fake_keyring, fake_cred_store) for assertions.
    """
    fake_keyring = MagicMock()
    fake_keyring.key = b"fake-key-32-bytes-for-fernet-key"

    fake_cred_store = MagicMock()
    fake_cred_store.connect = AsyncMock()
    fake_cred_store.close = AsyncMock()

    # get_full side_effect: return tg_creds for telegram, dc_creds for discord
    async def _get_full(platform: str, bot_id: str) -> tuple[str, str | None] | None:
        if platform == "telegram":
            return tg_creds
        if platform == "discord":
            return dc_creds
        return None

    fake_cred_store.get_full = AsyncMock(side_effect=_get_full)

    monkeypatch.setattr(
        stores_mod,
        "LyraKeyring",
        MagicMock(load_or_create=MagicMock(return_value=fake_keyring)),
    )
    monkeypatch.setattr(stores_mod, "CredentialStore", lambda **kwargs: fake_cred_store)
    return fake_keyring, fake_cred_store


def patch_bootstrap_common(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Patch shared bootstrap dependencies.

    Patches: auth_store, load_dotenv, agent stores, adapters.

    Returns fake_auth_store so callers can assert on it.
    """
    monkeypatch.setattr(main_mod, "load_dotenv", lambda: None)

    fake_auth_store = MagicMock()
    fake_auth_store.connect = AsyncMock()
    fake_auth_store.seed_from_config = AsyncMock()
    fake_auth_store.close = AsyncMock()
    monkeypatch.setattr(stores_mod, "AuthStore", lambda **kwargs: fake_auth_store)

    fake_agent_store = MagicMock()
    fake_agent_store.connect = AsyncMock()
    fake_agent_store.close = AsyncMock()
    fake_agent_store.get_bot_agent = MagicMock(return_value=None)
    # Return a fake AgentRow so agent_row_to_config can be reached
    _fake_agent_row = MagicMock()
    _fake_agent_row.name = "lyra_default"
    fake_agent_store.get = MagicMock(return_value=_fake_agent_row)
    fake_agent_store.set_bot_agent = AsyncMock()
    monkeypatch.setattr(stores_mod, "AgentStore", lambda **kwargs: fake_agent_store)

    # Bypass _resolve_bot_agent_map so credential-resolution tests are not blocked
    # by the agent-existence check. Builds the map directly from bot lists.
    async def _fake_resolve(agent_store, tg_bots, dc_bots):  # noqa: ANN001
        result = {}
        for bot_cfg in tg_bots:
            result[("telegram", bot_cfg.bot_id)] = "lyra_default"
        for bot_cfg in dc_bots:
            result[("discord", bot_cfg.bot_id)] = "lyra_default"
        return result

    monkeypatch.setattr(unified_mod, "_resolve_bot_agent_map", _fake_resolve)

    monkeypatch.setattr(
        unified_mod,
        "agent_row_to_config",
        lambda row, **kw: Agent(
            name=row.name if hasattr(row, "name") else "lyra_default",
            system_prompt="test",
            memory_namespace="test",
            llm_config=ModelConfig(backend="claude-cli"),
        ),
    )
    monkeypatch.setattr(
        wiring_mod, "TelegramAdapter", lambda **kwargs: _FakeTgAdapter()
    )
    monkeypatch.setattr(
        wiring_mod,
        "DiscordAdapter",
        lambda **kwargs: _FakeDcAdapter(),
    )

    mock_tg_auth = MagicMock()
    mock_dc_auth = MagicMock()
    _auth_results = iter([mock_tg_auth, mock_dc_auth])
    monkeypatch.setattr(
        AuthMiddleware,
        "from_config",
        classmethod(lambda cls, raw, section, store=None: next(_auth_results)),
    )

    _patch_nats_stubs(monkeypatch)

    return fake_auth_store


# ---------------------------------------------------------------------------
# test_main shared helpers (used by test_main_hub and test_main_auth)
# ---------------------------------------------------------------------------


def patch_all(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[Hub], MagicMock]:
    """Patch __main__ globals to avoid real network calls.

    Returns (captured_hubs, fake_auth_store) — callers can assert on the store mock.
    """
    captured: list[Hub] = []

    _OriginalHub = Hub

    class CapturingHub(_OriginalHub):  # type: ignore[misc]
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            captured.append(self)

    monkeypatch.setattr(unified_mod, "Hub", CapturingHub)

    class CapturingDcAdapter(_FakeDcAdapter):
        def __init__(self, **kwargs: object) -> None:
            shutdown = kwargs.pop("shutdown_event", None)  # type: ignore[assignment]
            super().__init__(shutdown_event=shutdown, **kwargs)  # type: ignore[arg-type]

    mock_tg_auth, mock_dc_auth = MagicMock(), MagicMock()
    _auth_results = iter([mock_tg_auth, mock_dc_auth])
    monkeypatch.setattr(main_mod, "load_dotenv", lambda: None)
    _synthetic_config = {
        "telegram": {"bots": [{"bot_id": "main"}]},
        "discord": {"bots": [{"bot_id": "main"}]},
        "auth": {
            "telegram_bots": [{"bot_id": "main", "default": "public"}],
            "discord_bots": [{"bot_id": "main", "default": "public"}],
        },
    }
    monkeypatch.setattr(main_mod, "_load_raw_config", lambda: _synthetic_config)
    _mock_auth_cls = MagicMock()
    _mock_auth_cls.from_config = MagicMock(
        side_effect=lambda *a, **kw: next(_auth_results)
    )
    _bot_auth_results = iter([mock_tg_auth, mock_dc_auth])
    _mock_auth_cls.from_bot_config = MagicMock(
        side_effect=lambda *a, **kw: next(_bot_auth_results)
    )
    monkeypatch.setattr(wiring_mod, "Authenticator", _mock_auth_cls)

    _fake_auth_store = MagicMock()
    _fake_auth_store.connect = AsyncMock()
    _fake_auth_store.seed_from_config = AsyncMock()
    _fake_auth_store.close = AsyncMock()
    monkeypatch.setattr(stores_mod, "AuthStore", lambda **kwargs: _fake_auth_store)

    _fake_agent_row = MagicMock()
    _fake_agent_row.name = "lyra_default"
    _fake_agent_store = MagicMock()
    _fake_agent_store.connect = AsyncMock()
    _fake_agent_store.close = AsyncMock()
    _fake_agent_store.get_bot_agent = MagicMock(return_value="lyra_default")
    _fake_agent_store.get = MagicMock(return_value=_fake_agent_row)
    _fake_agent_store.set_bot_agent = AsyncMock()
    monkeypatch.setattr(stores_mod, "AgentStore", lambda **kwargs: _fake_agent_store)

    _fake_keyring = MagicMock()
    _fake_keyring.key = b"fake-key-32-bytes-for-fernet-key"
    _fake_cred_store = MagicMock()
    _fake_cred_store.connect = AsyncMock()
    _fake_cred_store.close = AsyncMock()
    _fake_cred_store.get_full = AsyncMock(return_value=("fake-token", "fake-secret"))
    monkeypatch.setattr(
        stores_mod,
        "LyraKeyring",
        MagicMock(load_or_create=AsyncMock(return_value=_fake_keyring)),
    )
    monkeypatch.setattr(
        stores_mod, "CredentialStore", lambda **kwargs: _fake_cred_store
    )
    monkeypatch.setattr(
        unified_mod,
        "agent_row_to_config",
        lambda row, **kw: Agent(
            name=row.name,
            system_prompt="test",
            memory_namespace="test",
            llm_config=ModelConfig(backend="claude-cli"),
        ),
    )
    monkeypatch.setattr(
        wiring_mod, "TelegramAdapter", lambda **kwargs: _FakeTgAdapter(**kwargs)
    )
    monkeypatch.setattr(wiring_mod, "DiscordAdapter", CapturingDcAdapter)

    _patch_nats_stubs(monkeypatch)
    return captured, _fake_auth_store


def patch_auth_config_test(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shared setup for TestAuthConfig tests: mock auth/credential stores."""
    monkeypatch.setattr(main_mod, "load_dotenv", lambda: None)

    _fake_auth_store = MagicMock()
    _fake_auth_store.connect = AsyncMock()
    _fake_auth_store.seed_from_config = AsyncMock()
    _fake_auth_store.close = AsyncMock()
    monkeypatch.setattr(stores_mod, "AuthStore", lambda **kwargs: _fake_auth_store)

    _fake_agent_store = MagicMock()
    _fake_agent_store.connect = AsyncMock()
    _fake_agent_store.close = AsyncMock()
    _fake_agent_store.get_bot_agent = MagicMock(return_value=None)
    _fake_agent_store.get = MagicMock(return_value=None)
    _fake_agent_store.set_bot_agent = AsyncMock()
    monkeypatch.setattr(stores_mod, "AgentStore", lambda **kwargs: _fake_agent_store)
    monkeypatch.setattr(
        unified_mod,
        "_resolve_bot_agent_map",
        AsyncMock(return_value={("telegram", "main"): "lyra_default"}),
    )

    _fake_keyring = MagicMock()
    _fake_keyring.key = b"fake-key-32-bytes-for-fernet-key"
    _fake_cred_store = MagicMock()
    _fake_cred_store.connect = AsyncMock()
    _fake_cred_store.close = AsyncMock()
    _fake_cred_store.get_full = AsyncMock(return_value=("fake-token", "fake-secret"))
    monkeypatch.setattr(
        stores_mod,
        "LyraKeyring",
        MagicMock(load_or_create=AsyncMock(return_value=_fake_keyring)),
    )
    monkeypatch.setattr(
        stores_mod, "CredentialStore", lambda **kwargs: _fake_cred_store
    )

    _patch_nats_stubs(monkeypatch)


# ---------------------------------------------------------------------------
# Agent store fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def patch_agent_store(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Patch AgentStore in main_mod with a MagicMock. Returns the fake store."""
    _fake_agent_store = MagicMock()
    _fake_agent_store.connect = AsyncMock()
    _fake_agent_store.close = AsyncMock()
    _fake_agent_store.get_bot_agent = MagicMock(return_value=None)
    _fake_agent_store.get = MagicMock(return_value=None)
    _fake_agent_store.set_bot_agent = AsyncMock()
    monkeypatch.setattr(main_mod, "AgentStore", lambda **kwargs: _fake_agent_store)
    return _fake_agent_store
