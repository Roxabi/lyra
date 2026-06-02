"""Protocol conformance test — AuthStore satisfies AuthStoreProtocol.

Guards against silent protocol drift: if AuthStore drops or renames any method
required by the protocol, this test fails at import time.
"""

from __future__ import annotations

import pytest

from factory.core.stores.auth_store_protocol import AuthStoreProtocol


def test_auth_store_isinstance_check(tmp_path: pytest.TempPathFactory) -> None:
    """AuthStore satisfies AuthStoreProtocol (runtime_checkable check)."""
    from factory.infrastructure.stores.auth_store import AuthStore

    store = AuthStore(db_path=tmp_path / "grants.db")  # type: ignore[arg-type]
    assert isinstance(store, AuthStoreProtocol)


def test_auth_store_protocol_exported_from_package() -> None:
    """AuthStoreProtocol is importable from factory.core.stores."""
    from factory.core.stores import AuthStoreProtocol as _ASP

    assert _ASP is AuthStoreProtocol


def test_auth_store_protocol_has_required_methods() -> None:
    """AuthStoreProtocol defines exactly the expected interface methods.

    Re-adding or removing methods would silently break callers — guards against
    regression.
    """
    expected = {"check", "upsert", "revoke"}
    actual: set[str] = getattr(AuthStoreProtocol, "__protocol_attrs__", set())
    assert actual == expected
