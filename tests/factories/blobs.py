"""Shared blob-store test helpers.

Single source of truth for _make_mock_blob_store and _TEST_BLOB_REGISTRY.
Both tests/adapters/conftest.py and tests/agents/conftest.py import from here.

Design:
- _TEST_BLOB_REGISTRY: module-level dict used as side-table in mock_store.get/put.
- The autouse fixture clear_blob_registry resets it after each test to prevent
  cross-test state leakage.
- _make_mock_blob_store: builds a MagicMock conforming to BlobStorePort.get/put
  backed by _TEST_BLOB_REGISTRY.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

_TEST_BLOB_REGISTRY: dict[str, bytes] = {}


def _make_mock_blob_store() -> MagicMock:
    """Build a mock BlobStorePort backed by _TEST_BLOB_REGISTRY."""
    from roxabi_contracts import BlobRef

    async def _mock_get(store_key: str) -> bytes:
        return _TEST_BLOB_REGISTRY.get(store_key, b"mock-audio-bytes")

    async def _mock_put(data: bytes, *, mime: str, **kwargs: Any) -> BlobRef:
        ref = BlobRef(
            store_key="test-blob",
            content_hash="deadbeef",
            mime=mime,
            size=len(data),
            source="test",
        )
        _TEST_BLOB_REGISTRY[ref.store_key] = data
        return ref

    mock_store = MagicMock()
    mock_store.get = AsyncMock(side_effect=_mock_get)
    mock_store.put = AsyncMock(side_effect=_mock_put)
    return mock_store


@pytest.fixture(autouse=True)
def clear_blob_registry() -> Generator[None, None, None]:
    """Auto-use: clear _TEST_BLOB_REGISTRY after each test.

    Prevents cross-test leakage from shared registry state.
    """
    yield
    _TEST_BLOB_REGISTRY.clear()
