"""Protocol conformance test — ThreadStore satisfies ThreadStoreProtocol.

Guards against silent drift: if ThreadStore drops or renames any of the
6 methods required by the protocol, this test fails at import time.
"""

from __future__ import annotations

import pytest

from lyra.core.stores.thread_store_protocol import ThreadStoreProtocol


def test_thread_store_isinstance_check(tmp_path: pytest.TempPathFactory) -> None:
    """ThreadStore satisfies ThreadStoreProtocol (runtime_checkable check)."""
    from lyra.infrastructure.stores.thread_store import ThreadStore

    store = ThreadStore(db_path=tmp_path / "discord.db")  # type: ignore[arg-type]
    assert isinstance(store, ThreadStoreProtocol)


def test_thread_store_protocol_exported_from_package() -> None:
    """ThreadStoreProtocol is importable from lyra.core.stores."""
    from lyra.core.stores import ThreadStoreProtocol as _TSP

    assert _TSP is ThreadStoreProtocol
