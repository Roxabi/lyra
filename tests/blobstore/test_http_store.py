"""In-process ASGI tests for HttpBlobStore client (RED phase — T8 lands the impl)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from lyra.blobstore.serve import build_app
from roxabi_blobs.http_store import HttpBlobStore  # does not exist yet — RED

# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
async def asgi_store(tmp_path: Path) -> HttpBlobStore:
    """HttpBlobStore wired against an in-process FastAPI app via ASGITransport."""
    blob_root = tmp_path / "blobs"
    blob_root.mkdir()
    token = "test-bearer-token"
    app = build_app(token=token, blob_root=blob_root)  # T7/T8 extend build_app
    transport = httpx.ASGITransport(app=app)
    return HttpBlobStore(
        base_url="http://testserver",
        token=token,
        _transport=transport,
    )


# ---------------------------------------------------------------------------
# N1/N2 — PUT then GET roundtrip
# ---------------------------------------------------------------------------


class TestHttpBlobStoreRoundtrip:
    async def test_http_blob_store_put_then_get_roundtrip(
        self, asgi_store: HttpBlobStore
    ) -> None:
        """PUT a payload via HttpBlobStore then GET it back — bytes must be equal."""
        # Arrange
        payload = b"hello blobstore roundtrip"
        # Act
        async with asgi_store:
            ref = await asgi_store.put(payload, mime="text/plain", source="test")
            data = await asgi_store.get(ref.store_key)
        # Assert
        assert data == payload


# ---------------------------------------------------------------------------
# N2/N3 — exists returns True after PUT, False on unknown hash
# ---------------------------------------------------------------------------


class TestHttpBlobStoreExists:
    async def test_http_blob_store_exists_returns_true_after_put(
        self, asgi_store: HttpBlobStore
    ) -> None:
        """exists() returns a BlobRef after a PUT and None for an unknown hash."""
        # Arrange
        payload = b"existence check payload"
        # Act
        async with asgi_store:
            ref = await asgi_store.put(
                payload, mime="application/octet-stream", source="test"
            )
            found = await asgi_store.exists(ref.content_hash)
            missing = await asgi_store.exists("deadbeef" * 8)
        # Assert
        assert found is not None
        assert missing is None

    async def test_http_blob_store_exists_sentinel_content_hash_is_empty(
        self, asgi_store: HttpBlobStore
    ) -> None:
        """exists() sentinel BlobRef has empty content_hash — HEAD has no body."""
        # Arrange
        payload = b"sentinel content_hash check"
        # Act
        async with asgi_store:
            ref = await asgi_store.put(
                payload, mime="application/octet-stream", source="test"
            )
            found = await asgi_store.exists(ref.content_hash)
        # Assert
        assert found is not None
        assert found.content_hash == ""


# ---------------------------------------------------------------------------
# N4 — delete removes blob
# ---------------------------------------------------------------------------


class TestHttpBlobStoreDelete:
    async def test_http_blob_store_delete_removes_blob(
        self, asgi_store: HttpBlobStore
    ) -> None:
        """DELETE removes the blob_refs row; exists() returns None afterwards."""
        # Arrange
        payload = b"payload to delete"
        # Act
        async with asgi_store:
            ref = await asgi_store.put(payload, mime="text/plain", source="test")
            assert ref.id is not None  # blob_ref_id from BlobRef
            await asgi_store.delete(ref.id)
            found = await asgi_store.exists(ref.content_hash)
        # Assert
        assert found is None


# ---------------------------------------------------------------------------
# SC-Tests-2 — ASGI transport, no real network
# ---------------------------------------------------------------------------


class TestHttpBlobStoreAsgiTransport:
    def test_http_blob_store_uses_asgi_transport_no_real_network(
        self, tmp_path: Path
    ) -> None:
        """HttpBlobStore.__init__ accepts a _transport kwarg for test-only seam.

        SC-Tests-2: no real network — the ASGI transport is injected in-process.
        This test verifies the constructor accepts the seam and wires it into the
        httpx.AsyncClient; deleting the _transport parameter from HttpBlobStore
        would cause a TypeError here.
        """
        # Arrange
        app = build_app(token="tok", blob_root=tmp_path)
        transport = httpx.ASGITransport(app=app)
        # Act — constructor must accept _transport keyword-only without raising
        store = HttpBlobStore(
            base_url="http://testserver",
            token="tok",
            _transport=transport,
        )
        # Assert — the transport is wired (accessing internal attribute is intentional
        # here: we are testing the seam itself, not public behaviour)
        assert store._transport is transport  # noqa: SLF001
