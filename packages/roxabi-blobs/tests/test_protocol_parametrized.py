"""Parametrized Protocol-equivalence tests: FsBlobStore vs HttpBlobStore.

SC-Tests-1: same test body runs against both backends — proves Protocol
equivalence. The HttpBlobStore fixture variant (RED) fails at import until T8
lands the implementation.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from roxabi_blobs.http_store import (  # does not exist yet — RED
    HttpBlobStore,
)

from roxabi_blobs import BlobStore, FsBlobStore

# ---------------------------------------------------------------------------
# Parametrized store fixture
# ---------------------------------------------------------------------------


@pytest.fixture(
    params=["fs", "http"],
    ids=["FsBlobStore", "HttpBlobStore"],
)
async def store(
    request: pytest.FixtureRequest, tmp_path: Path
) -> AsyncIterator[BlobStore]:
    """Yields a BlobStore instance — FsBlobStore or HttpBlobStore (in-process)."""
    if request.param == "fs":
        async with FsBlobStore(tmp_path) as s:
            yield s
    else:
        # HttpBlobStore against an in-process FastAPI app via ASGITransport.
        # Import deferred to fixture body so FsBlobStore variant still collects
        # even if http_store module is absent (RED phase masks collection via the
        # top-level import above, but the pattern is correct for GREEN phase).
        from lyra.blobstore.serve import build_app  # noqa: PLC0415

        blob_root = tmp_path / "blobs"
        blob_root.mkdir()
        token = "test-proto-token"
        app = build_app(token=token, blob_root=blob_root)
        transport = httpx.ASGITransport(app=app)
        store_instance = HttpBlobStore(
            base_url="http://testserver",
            token=token,
            _transport=transport,
        )
        async with store_instance:
            yield store_instance


# ---------------------------------------------------------------------------
# Protocol-equivalence test class
# ---------------------------------------------------------------------------


class TestBlobStoreProtocol:
    """Same test body runs against FsBlobStore and HttpBlobStore (SC-Tests-1)."""

    async def test_put_returns_blob_ref_with_content_hash(
        self, store: BlobStore
    ) -> None:
        """put() returns a BlobRef whose content_hash matches sha256 of the input."""
        import hashlib

        # Arrange
        payload = b"protocol equivalence test payload"
        expected_hash = hashlib.sha256(payload).hexdigest()
        # Act
        ref = await store.put(payload, mime="text/plain", source="test")
        # Assert
        assert ref.content_hash == expected_hash
        assert ref.size == len(payload)
        assert ref.mime == "text/plain"

    async def test_get_after_put_returns_same_bytes(self, store: BlobStore) -> None:
        """get(store_key) returns the exact bytes previously put."""
        # Arrange
        payload = b"get after put protocol check"
        # Act
        ref = await store.put(payload, mime="application/octet-stream", source="test")
        data = await store.get(ref.store_key)
        # Assert
        assert data == payload

    async def test_exists_returns_true_after_put_false_before(
        self, store: BlobStore
    ) -> None:
        """exists() returns BlobRef after put, None for unknown content_hash."""
        # Arrange
        payload = b"exists check"
        # Act
        ref = await store.put(payload, mime="text/plain", source="test")
        found = await store.exists(ref.content_hash)
        missing = await store.exists("aaaa" * 16)  # 64-char hex that was never put
        # Assert
        assert found is not None
        assert found.content_hash == ref.content_hash
        assert missing is None

    async def test_delete_removes_blob_from_store(self, store: BlobStore) -> None:
        """delete(blob_ref_id) causes exists() to return None for that hash."""
        # Arrange
        payload = b"blob to delete"
        # Act
        ref = await store.put(payload, mime="text/plain", source="test")
        blob_ref_id = ref.id  # row id from BlobRef
        await store.delete(blob_ref_id)
        found = await store.exists(ref.content_hash)
        # Assert
        assert found is None

    def test_runtime_checkable_protocol_accepts_instance(
        self, store: BlobStore
    ) -> None:
        """isinstance(store, BlobStore) is True — SC-Code-6 runtime_checkable.

        Deleting @runtime_checkable from BlobStore would cause this to raise
        TypeError, not return False — any change to the Protocol decorator
        is caught here.
        """
        # Arrange + Act + Assert
        assert isinstance(store, BlobStore)
