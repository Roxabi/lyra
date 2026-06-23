"""Protocol conformance — AgentGrantStore satisfies its core ports.

Guards against silent protocol drift: if AgentGrantStore drops or renames a
method required by AgentAuthorizer (the narrow read port the hub middleware
depends on, ADR-090 §4) or AgentGrantStoreProtocol (the full store contract),
these checks fail.
"""

from __future__ import annotations

from pathlib import Path

from factory.core.auth import AgentAuthorizer
from factory.core.stores import AgentGrantStoreProtocol


def test_store_satisfies_agent_authorizer(tmp_path: Path) -> None:
    """AgentGrantStore is usable wherever the narrow read port is required."""
    from factory.infrastructure.stores.identity.agent_grant_store import AgentGrantStore

    store = AgentGrantStore(db_path=str(tmp_path / "auth.db"))
    assert isinstance(store, AgentAuthorizer)


def test_store_satisfies_full_store_protocol(tmp_path: Path) -> None:
    """AgentGrantStore conforms to the full grant-store contract."""
    from factory.infrastructure.stores.identity.agent_grant_store import AgentGrantStore

    store = AgentGrantStore(db_path=str(tmp_path / "auth.db"))
    assert isinstance(store, AgentGrantStoreProtocol)


def test_protocols_exported_from_packages() -> None:
    """The ports are importable from their canonical packages."""
    from factory.core.auth import AgentAuthorizer as _AA
    from factory.core.stores import AgentGrantStoreProtocol as _AGSP

    assert _AA is AgentAuthorizer
    assert _AGSP is AgentGrantStoreProtocol


def test_read_port_is_narrow() -> None:
    """AgentAuthorizer exposes only authorize — writes belong to the store port."""
    attrs: set[str] = getattr(AgentAuthorizer, "__protocol_attrs__", set())
    assert attrs == {"authorize"}


def test_store_protocol_has_required_methods() -> None:
    """AgentGrantStoreProtocol = read port + operator write surface."""
    attrs: set[str] = getattr(AgentGrantStoreProtocol, "__protocol_attrs__", set())
    assert attrs == {"authorize", "list_grants", "grant", "revoke"}


def test_store_sync_async_shape_is_stable() -> None:
    """Reads are sync (cache, never block the loop); writes are async.

    Complements the structural isinstance checks, which ignore async-ness — this
    catches a sync↔async drift that would still satisfy the Protocol.
    """
    import inspect

    from factory.infrastructure.stores.identity.agent_grant_store import AgentGrantStore

    for sync_method in ("authorize", "list_grants"):
        assert not inspect.iscoroutinefunction(getattr(AgentGrantStore, sync_method))
    for async_method in ("grant", "revoke"):
        assert inspect.iscoroutinefunction(getattr(AgentGrantStore, async_method))
