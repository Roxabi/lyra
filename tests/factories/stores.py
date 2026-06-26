"""Store factory helpers and fixtures for tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from factory.core.lifecycle.circuit_breaker import CircuitBreaker, CircuitRegistry
from factory.infrastructure.stores.identity.agent_grant_store import AgentGrantStore
from factory.infrastructure.stores.identity.auth_store import AuthStore
from factory.infrastructure.stores.identity.pairing import PairingConfig, PairingManager
from factory.infrastructure.stores.identity.user_store import UserStore
from factory.infrastructure.stores.registry.agent_store import AgentRow, AgentStore
from tests.helpers.bot_store import make_bot_store

__all__ = [
    "agent_grant_store",
    "agent_store",
    "auth_store",
    "bot_store",
    "json_agent_store",
    "make_agent_grant_store",
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


async def make_agent_grant_store(tmp_path: Path) -> AgentGrantStore:
    """Create and connect a real AgentGrantStore backed by a tmp file DB.

    Shares the ``auth.db`` filename with AuthStore (ADR-090 §3); a tmp file keeps
    each test isolated. Prefer the ``agent_grant_store`` fixture for new tests.
    """
    store = AgentGrantStore(db_path=str(tmp_path / "auth.db"))
    await store.connect()
    return store


@pytest.fixture
async def agent_grant_store(tmp_path: Path):
    """Fixture-based AgentGrantStore with automatic teardown."""
    store = await make_agent_grant_store(tmp_path)
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
    from factory.core.stores.json_agent_store import JsonAgentStore

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
_open_pairing_user_stores: list[UserStore] = []
_open_pairing_grant_stores: list[AgentGrantStore] = []

PAIRING_TEST_AGENT = "test_agent"


async def make_pairing_auth_store(db_path: str = ":memory:") -> AuthStore:
    """Build and connect a real AuthStore for legacy pairing-adjacent tests."""
    store = AuthStore(db_path=db_path)
    await store.connect()
    _open_pairing_stores.append(store)
    return store


async def make_pairing_identity_stores(
    db_path: str = ":memory:",
) -> tuple[UserStore, AgentGrantStore]:
    """Build UserStore + AgentGrantStore sharing one auth.db for pairing tests."""
    user_store = UserStore(db_path=db_path)
    await user_store.connect()
    grant_store = AgentGrantStore(db_path=db_path, user_store=user_store)
    await grant_store.connect()
    _open_pairing_user_stores.append(user_store)
    _open_pairing_grant_stores.append(grant_store)
    return user_store, grant_store


_open_pairing_managers: list[PairingManager] = []


async def make_pairing_pm(  # noqa: PLR0913
    enabled: bool = True,
    max_pending: int = 3,
    rate_limit_attempts: int = 5,
    rate_limit_window: int = 300,
    session_max_age_days: int = 30,
    ttl_seconds: int = 3600,
    grant_store: AgentGrantStore | None = None,
    user_store: UserStore | None = None,
) -> PairingManager:
    """Build and connect a PairingManager backed by an in-memory SQLite DB."""
    if grant_store is None or user_store is None:
        user_store, grant_store = await make_pairing_identity_stores()

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
        grant_store=grant_store,
        user_store=user_store,
    )
    await pm.connect()
    _open_pairing_managers.append(pm)
    return pm
