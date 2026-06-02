"""Protocol conformance test — ThreadStore satisfies ThreadStoreProtocol.

Guards against silent protocol drift: if ThreadStore drops or renames any method
required by the protocol, this test fails at import time.
"""

from __future__ import annotations

import pytest

from factory.core.stores.thread_store_protocol import ThreadStoreProtocol


def test_thread_store_isinstance_check(tmp_path: pytest.TempPathFactory) -> None:
    """ThreadStore satisfies ThreadStoreProtocol (runtime_checkable check)."""
    from factory.infrastructure.stores.thread_store import ThreadStore

    store = ThreadStore(db_path=tmp_path / "discord.db")  # type: ignore[arg-type]
    assert isinstance(store, ThreadStoreProtocol)


def test_thread_store_protocol_exported_from_package() -> None:
    """ThreadStoreProtocol is importable from factory.core.stores."""
    from factory.core.stores import ThreadStoreProtocol as _TSP

    assert _TSP is ThreadStoreProtocol


def test_thread_store_protocol_has_no_close_method() -> None:
    """ADR-063: close() was explicitly removed from ThreadStoreProtocol.

    Re-adding it would silently break teardown ownership — guards against regression.
    """
    expected = {"get_thread_ids", "is_owned", "get_session", "claim", "update_session"}
    actual: set[str] = getattr(ThreadStoreProtocol, "__protocol_attrs__", set())
    assert actual == expected
