"""Store factory helpers and fixtures for tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from lyra.core.lifecycle.circuit_breaker import CircuitBreaker, CircuitRegistry
from lyra.infrastructure.stores.agent_store import AgentRow, AgentStore
from lyra.infrastructure.stores.auth_store import AuthStore
from lyra.infrastructure.stores.pairing import PairingConfig, PairingManager
from tests.helpers.bot_store import make_bot_store

__all__ = [
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


def make_circuit_registry(**overrides) -> CircuitRegistry:
    """Build a CircuitRegistry with default CBs for all services."""
    registry = CircuitRegistry()
    defaults = {
        "claude-cli": CircuitBreaker(
            "claude-cli", failure_threshold=3, recovery_timeout=60
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


def make_agent_row(name: str = "test-agent") -> AgentRow:
    """Return a minimal valid AgentRow for the given name."""
    return AgentRow(
        name=name,
        backend="claude-cli",
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


@pytest.fixture
async def bot_store(tmp_path: Path):
    """Fixture-based BotStore with automatic teardown."""
    store = await make_bot_store(tmp_path)
    try:
        yield store
    finally:
        await store.close()


_open_pairing_stores: list[AuthStore] = []


async def make_pairing_auth_store(db_path: str = ":memory:") -> AuthStore:
    """Build and connect a real AuthStore for pairing tests."""
    store = AuthStore(db_path=db_path)
    await store.connect()
    _open_pairing_stores.append(store)
    return store


_open_pairing_managers: list[PairingManager] = []


async def make_pairing_pm(  # noqa: PLR0913
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
