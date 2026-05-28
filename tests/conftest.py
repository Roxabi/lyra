"""Shared pytest fixtures for the Lyra test suite."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.circuit_breaker import CircuitBreaker, CircuitRegistry
from lyra.core.hub import Hub

# DI factory re-exports from bootstrap factories
from tests.factories.bootstrap import (  # noqa: F401
    _FakeDcAdapter,
    _FakeDp,
    _FakeTgAdapter,
    _reset_version_check_log_state,
    make_fake_audit_sink,
    make_fake_auth_middleware,
    make_fake_auth_store,
    make_fake_bot_store,
    make_fake_capturing_hub,
    make_fake_credentials,
    make_fake_dc_adapter,
    make_fake_hub,
    make_fake_lifecycle_resources,
    make_fake_nats_bus,
    make_fake_nats_client,
    make_fake_tg_adapter,
    make_fake_tg_adapter_mock,
    make_fake_wired_adapters,
)

__all__ = [
    "_FakeDcAdapter",
    "_FakeDp",
    "_FakeTgAdapter",
    "_reset_version_check_log_state",
    "make_fake_audit_sink",
    "make_fake_auth_middleware",
    "make_fake_auth_store",
    "make_fake_bot_store",
    "make_fake_capturing_hub",
    "make_fake_credentials",
    "make_fake_dc_adapter",
    "make_fake_hub",
    "make_fake_lifecycle_resources",
    "make_fake_nats_bus",
    "make_fake_nats_client",
    "make_fake_tg_adapter",
    "make_fake_tg_adapter_mock",
    "make_fake_wired_adapters",
]

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


async def _drain(pool: Any, *, timeout: float = TIMEOUT_IO) -> None:
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
    for name in ("claude-cli", "telegram", "discord", "hub"):
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
# Legacy monkeypatch helpers (used by test_main_auth.py / test_main_hub.py)
# ---------------------------------------------------------------------------


def patch_all(  # noqa: PLR0915 — legacy test harness, many monkeypatch statements
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[Any], MagicMock]:
    """Patch __main__ globals to avoid real network calls.

    Returns (captured_hubs, fake_auth_store) — callers can assert on the store mock.
    """
    from typing import cast
    from unittest.mock import AsyncMock, MagicMock

    import lyra.__main__ as main_mod
    import lyra.bootstrap.bootstrap_stores as stores_mod
    import lyra.bootstrap.factory.wiring_helpers as wiring_helpers_mod
    import lyra.bootstrap.wiring.bootstrap_wiring as wiring_mod
    from lyra.core.agent import Agent
    from lyra.core.agent.agent_config import ModelConfig
    from lyra.core.hub import Hub
    from tests.factories.bootstrap import _FakeDcAdapter, _FakeTgAdapter

    captured: list[Hub] = []

    _OriginalHub = Hub

    class CapturingHub(_OriginalHub):  # type: ignore[misc]
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            captured.append(self)

    monkeypatch.setattr(wiring_helpers_mod, "Hub", CapturingHub)

    class CapturingDcAdapter(_FakeDcAdapter):
        def __init__(self, **kwargs: object) -> None:
            shutdown = cast("asyncio.Event | None", kwargs.pop("shutdown_event", None))
            super().__init__(shutdown_event=shutdown, **kwargs)

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
    _mock_auth_cls.from_bot_store = MagicMock(
        side_effect=lambda *a, **kw: next(_bot_auth_results)
    )
    monkeypatch.setattr(wiring_mod, "Authenticator", _mock_auth_cls)

    _fake_auth_store = MagicMock()
    _fake_auth_store.connect = AsyncMock()
    _fake_auth_store.seed_from_config = AsyncMock()
    _fake_auth_store.close = AsyncMock()
    monkeypatch.setattr(stores_mod, "AuthStore", lambda **kwargs: _fake_auth_store)

    from lyra.core.agent.bot_models import BotRow

    _fake_bot_store = MagicMock()
    _fake_bot_store.connect = AsyncMock()
    _fake_bot_store.close = AsyncMock()
    _fake_bot_store.get_all = MagicMock(
        return_value=[
            BotRow(platform="telegram", bot_id="main", agent="lyra_default"),
            BotRow(platform="discord", bot_id="main", agent="lyra_default"),
        ]
    )
    monkeypatch.setattr(stores_mod, "BotStore", lambda **kwargs: _fake_bot_store)

    _fake_agent_row = MagicMock()
    _fake_agent_row.name = "lyra_default"
    _fake_agent_store = MagicMock()
    _fake_agent_store.connect = AsyncMock()
    _fake_agent_store.close = AsyncMock()
    _fake_agent_store.get_bot_agent = MagicMock(return_value="lyra_default")
    _fake_agent_store.get = MagicMock(return_value=_fake_agent_row)
    _fake_agent_store.set_bot_agent = AsyncMock()
    monkeypatch.setattr(stores_mod, "AgentStore", lambda **kwargs: _fake_agent_store)

    import lyra.bootstrap.credentials as credentials_mod

    monkeypatch.setattr(
        credentials_mod,
        "load_bot_token",
        lambda platform, bot_id: ("fake-token", "fake-secret"),
    )
    monkeypatch.setattr(
        wiring_helpers_mod,
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

    # Inline _patch_nats_stubs
    import lyra.bootstrap.factory.unified as unified_mod
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
    monkeypatch.setattr(wiring_helpers_mod, "NatsBus", lambda **kw: fake_nats_bus)
    fake_audit_sink = MagicMock()
    fake_audit_sink.provision = AsyncMock()
    fake_audit_sink.emit = AsyncMock()
    _make_sink = lambda: fake_audit_sink  # noqa: E731
    monkeypatch.setattr(wiring_helpers_mod, "JetStreamAuditSink", _make_sink)
    monkeypatch.setenv("NATS_URL", "nats://localhost:4222")
    monkeypatch.setenv("LYRA_HEALTH_PORT", "0")
    monkeypatch.setenv("LYRA_VAULT_DIR", "/tmp/fake-vault")

    return captured, _fake_auth_store


def patch_auth_config_test(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shared setup for TestAuthConfig tests: mock auth/credential stores."""
    from unittest.mock import AsyncMock, MagicMock

    import lyra.__main__ as main_mod
    import lyra.bootstrap.bootstrap_stores as stores_mod
    import lyra.bootstrap.factory.agent_factory as agent_factory_mod
    from lyra.core.agent.bot_models import BotRow

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

    _fake_bot_store = MagicMock()
    _fake_bot_store.connect = AsyncMock()
    _fake_bot_store.close = AsyncMock()
    _fake_bot_store.get = MagicMock(
        side_effect=lambda platform, bot_id: (
            BotRow(
                platform=platform,
                bot_id=bot_id,
                agent="lyra_default",
                default_trust="public",
            )
            if (platform, bot_id) == ("telegram", "main")
            else None
        )
    )
    monkeypatch.setattr(stores_mod, "BotStore", lambda **kwargs: _fake_bot_store)
    monkeypatch.setattr(
        agent_factory_mod,
        "_resolve_bot_agent_map",
        AsyncMock(return_value={("telegram", "main"): "lyra_default"}),
    )

    import lyra.bootstrap.credentials as credentials_mod

    monkeypatch.setattr(
        credentials_mod,
        "load_bot_token",
        lambda platform, bot_id: ("fake-token", "fake-secret"),
    )

    # Inline _patch_nats_stubs
    import lyra.bootstrap.factory.unified as unified_mod
    import lyra.bootstrap.factory.wiring_helpers as wiring_helpers_mod
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
    monkeypatch.setattr(wiring_helpers_mod, "NatsBus", lambda **kw: fake_nats_bus)
    fake_audit_sink = MagicMock()
    fake_audit_sink.provision = AsyncMock()
    fake_audit_sink.emit = AsyncMock()
    _make_sink = lambda: fake_audit_sink  # noqa: E731
    monkeypatch.setattr(wiring_helpers_mod, "JetStreamAuditSink", _make_sink)
    monkeypatch.setenv("NATS_URL", "nats://localhost:4222")
    monkeypatch.setenv("LYRA_HEALTH_PORT", "0")
    monkeypatch.setenv("LYRA_VAULT_DIR", "/tmp/fake-vault")


# ---------------------------------------------------------------------------
# Agent store fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def patch_agent_store(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Patch AgentStore in main_mod with a MagicMock. Returns the fake store."""
    import lyra.__main__ as main_mod

    _fake_agent_store = MagicMock()
    _fake_agent_store.connect = AsyncMock()
    _fake_agent_store.close = AsyncMock()
    _fake_agent_store.get_bot_agent = MagicMock(return_value=None)
    _fake_agent_store.get = MagicMock(return_value=None)
    _fake_agent_store.set_bot_agent = AsyncMock()
    monkeypatch.setattr(main_mod, "AgentStore", lambda **kwargs: _fake_agent_store)
    return _fake_agent_store
