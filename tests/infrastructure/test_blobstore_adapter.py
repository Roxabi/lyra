"""Integration tests for HttpBlobStoreAdapter.

Injects a fake wrapped store (matching the real roxabi_blobs.HttpBlobStore.put
signature exactly) and asserts:
  1. Happy path — put() converts STORAGE BlobRef to WIRE BlobRef, preserving
     created_at and all provenance fields.
  2. PENDING guard — put() raises ValueError when the wrapped store returns a
     ref with store_key == PENDING_STORE_KEY (real ingest must yield a real key).
  3. Delegation — get() / exists() delegate to the wrapped store.
  4. exists() sentinel mapping — sentinel storage ref maps to None (not a wire BlobRef).
  5. Protocol conformance — HttpBlobStoreAdapter satisfies BlobStorePort.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import cast
from unittest.mock import AsyncMock

import pytest

from roxabi_blobs import HttpBlobStore

# ---------------------------------------------------------------------------
# Shared fake factory
# ---------------------------------------------------------------------------


def _make_storage_ref(
    store_key: str = "sha256:abc123",
    *,
    include_platform_ref: bool = False,
    is_sentinel: bool = False,
    content_hash_override: str | None = None,
) -> object:
    """Return a STORAGE-shaped ref object (roxabi_blobs.BlobRef duck-type).

    model_dump(exclude={"id", "is_sentinel"}) must yield all wire fields.
    """
    from roxabi_blobs.models import BlobRef as StorageBlobRef

    created_at = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    if is_sentinel:
        # Sentinel refs have empty content_hash; is_sentinel=True allows this.
        return StorageBlobRef(
            store_key=store_key,
            content_hash="",
            mime="audio/ogg",
            size=0,
            source="tts",
            id=None,
            created_at=created_at,
            is_sentinel=True,
        )
    content_hash = content_hash_override or "abc123deadbeef" * 3 + "ab"  # 32-hex chars
    return StorageBlobRef(
        store_key=store_key,
        content_hash=content_hash,
        mime="audio/ogg",
        size=42,
        source="tts",
        filename="voice.ogg",
        platform_ref="tg:file_id_abc" if include_platform_ref else None,
        platform_message_id="msg-99" if include_platform_ref else None,
        id=7,
        created_at=created_at,
        is_sentinel=False,
    )


def _make_fake_http_store(
    put_return: object | None = None,
    get_return: bytes = b"audio bytes",
    exists_return: object | None = None,
) -> HttpBlobStore:
    """Return an AsyncMock typed as HttpBlobStore via cast.

    Using cast() rather than MagicMock(spec=HttpBlobStore) because spec-mocking
    a class that requires live HTTP resources at construction time is fragile.
    The cast is sound: AsyncMock satisfies every attribute access pyright sees
    through the HttpBlobStore type.
    """
    fake = AsyncMock()
    if put_return is not None:
        fake.put.return_value = put_return
    fake.get.return_value = get_return
    fake.exists.return_value = exists_return
    return cast(HttpBlobStore, fake)


# ---------------------------------------------------------------------------
# T1 — Happy path: put() converts storage→wire BlobRef
# ---------------------------------------------------------------------------


class TestHttpBlobStoreAdapterPut:
    async def test_put_returns_wire_blobref(self) -> None:
        """put() returns a roxabi_contracts.BlobRef converted from storage ref."""
        # Arrange
        from lyra.infrastructure.blobstore_adapter import HttpBlobStoreAdapter
        from roxabi_contracts.blob_ref import BlobRef as WireBlobRef

        storage_ref = _make_storage_ref(store_key="sha256:abc123")
        fake_store = _make_fake_http_store(put_return=storage_ref)
        adapter = HttpBlobStoreAdapter(fake_store)

        # Act
        result = await adapter.put(b"hello tts", mime="audio/ogg", source="tts")

        # Assert — result is a wire BlobRef
        assert isinstance(result, WireBlobRef)

    async def test_put_preserves_created_at(self) -> None:
        """put() carries created_at verbatim from storage ref (not a fresh default).

        Negative guard: if from_store_ref is replaced with a fresh BlobRef() call
        that uses default_factory for created_at, this test will fail because
        the timestamp won't match the storage ref's value.
        """
        # Arrange
        from lyra.infrastructure.blobstore_adapter import HttpBlobStoreAdapter

        expected_created_at = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        storage_ref = _make_storage_ref(store_key="sha256:abc123")
        fake_store = _make_fake_http_store(put_return=storage_ref)
        adapter = HttpBlobStoreAdapter(fake_store)

        # Act
        result = await adapter.put(b"hello tts", mime="audio/ogg", source="tts")

        # Assert — created_at is the storage ref's value, not a fresh datetime.now()
        assert result.created_at == expected_created_at

    async def test_put_carries_provenance_fields(self) -> None:
        """put() carries platform_ref, platform_message_id, filename, source, store_key."""  # noqa: E501
        # Arrange
        from lyra.infrastructure.blobstore_adapter import HttpBlobStoreAdapter

        storage_ref = _make_storage_ref(
            store_key="sha256:abc123", include_platform_ref=True
        )
        fake_store = _make_fake_http_store(put_return=storage_ref)
        adapter = HttpBlobStoreAdapter(fake_store)

        # Act
        result = await adapter.put(
            b"hello tts",
            mime="audio/ogg",
            source="tts",
            filename="voice.ogg",
            platform_ref="tg:file_id_abc",
            platform_message_id="msg-99",
        )

        # Assert — wire fields carried through
        assert result.filename == "voice.ogg"
        assert result.platform_ref == "tg:file_id_abc"
        assert result.platform_message_id == "msg-99"
        assert result.source == "tts"
        assert result.store_key == "sha256:abc123"

    async def test_put_delegates_to_wrapped_store(self) -> None:
        """put() delegates to the wrapped store with the exact same arguments."""
        # Arrange
        from lyra.infrastructure.blobstore_adapter import HttpBlobStoreAdapter

        storage_ref = _make_storage_ref()
        fake_store = _make_fake_http_store(put_return=storage_ref)
        adapter = HttpBlobStoreAdapter(fake_store)

        # Act
        await adapter.put(
            b"data",
            mime="image/png",
            source="discord",
            filename="img.png",
            platform_ref="dc:ref",
            platform_message_id="dc-msg-1",
        )

        # Assert — wrapped store's put was called once with matching kwargs
        cast(AsyncMock, fake_store.put).assert_called_once_with(
            b"data",
            mime="image/png",
            source="discord",
            filename="img.png",
            platform_ref="dc:ref",
            platform_message_id="dc-msg-1",
        )


# ---------------------------------------------------------------------------
# T2 — PENDING guard: put() raises ValueError when store returns PENDING_STORE_KEY
# ---------------------------------------------------------------------------


class TestHttpBlobStoreAdapterPendingGuard:
    async def test_put_raises_when_store_key_is_pending(self) -> None:
        """put() raises ValueError when store_key == PENDING_STORE_KEY.

        Negative guard: if the PENDING check is deleted from HttpBlobStoreAdapter.put,
        this test will fail because no ValueError will be raised.
        """
        # Arrange
        from lyra.infrastructure.blobstore_adapter import HttpBlobStoreAdapter
        from roxabi_blobs.models import BlobRef as StorageBlobRef
        from roxabi_contracts.blob_ref import PENDING_STORE_KEY

        # Build a storage ref that simulates the pathological case:
        # store returns PENDING_STORE_KEY — a contract violation for live ingest.
        # StorageBlobRef requires is_sentinel=True when content_hash is "".
        pending_ref = StorageBlobRef(
            store_key=PENDING_STORE_KEY,
            content_hash="",
            mime="audio/ogg",
            size=0,
            source="tts",
            id=None,
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            is_sentinel=True,
        )
        fake_store = _make_fake_http_store(put_return=pending_ref)
        adapter = HttpBlobStoreAdapter(fake_store)

        # Act / Assert — ValueError must be raised; no value returned
        with pytest.raises(ValueError):
            await adapter.put(b"data", mime="audio/ogg", source="tts")


# ---------------------------------------------------------------------------
# T3 — Delegation: get / exists pass through to wrapped store
# ---------------------------------------------------------------------------


class TestHttpBlobStoreAdapterDelegation:
    async def test_get_delegates_to_wrapped_store(self) -> None:
        """get() returns bytes directly from the wrapped store."""
        # Arrange
        from lyra.infrastructure.blobstore_adapter import HttpBlobStoreAdapter

        payload = b"raw bytes from store"
        fake_store = _make_fake_http_store(get_return=payload)
        adapter = HttpBlobStoreAdapter(fake_store)

        # Act
        result = await adapter.get("sha256:abc123")

        # Assert
        cast(AsyncMock, fake_store.get).assert_called_once_with("sha256:abc123")
        assert result == payload

    async def test_exists_returns_none_when_wrapped_store_returns_none(self) -> None:
        """exists() returns None when the wrapped store reports no match."""
        # Arrange
        from lyra.infrastructure.blobstore_adapter import HttpBlobStoreAdapter

        fake_store = _make_fake_http_store(exists_return=None)
        adapter = HttpBlobStoreAdapter(fake_store)

        # Act
        result = await adapter.exists("nonexistent-hash")

        # Assert
        cast(AsyncMock, fake_store.exists).assert_called_once_with("nonexistent-hash")
        assert result is None

    async def test_exists_converts_full_storage_ref_to_wire_blobref(self) -> None:
        """exists() converts a non-sentinel storage ref to a wire BlobRef."""
        # Arrange
        from lyra.infrastructure.blobstore_adapter import HttpBlobStoreAdapter
        from roxabi_contracts.blob_ref import BlobRef as WireBlobRef

        storage_ref = _make_storage_ref(store_key="sha256:abc123")
        fake_store = _make_fake_http_store(exists_return=storage_ref)
        adapter = HttpBlobStoreAdapter(fake_store)

        # Act
        result = await adapter.exists("abc123deadbeef" * 3 + "ab")

        # Assert
        assert result is not None
        assert isinstance(result, WireBlobRef)
        assert result.store_key == "sha256:abc123"

    async def test_exists_returns_none_for_sentinel_storage_ref(self) -> None:
        """exists() returns None when the wrapped store returns a sentinel BlobRef.

        Negative guard: the adapter maps is_sentinel=True to None because the
        wire validator rejects content_hash="" when store_key != PENDING_STORE_KEY.
        If this mapping is removed, from_store_ref will raise a ValidationError —
        or worse, a corrupt BlobRef with content_hash="" will propagate downstream.
        """
        # Arrange
        from lyra.infrastructure.blobstore_adapter import HttpBlobStoreAdapter

        sentinel_ref = _make_storage_ref(store_key="sha256:existing", is_sentinel=True)
        fake_store = _make_fake_http_store(exists_return=sentinel_ref)
        adapter = HttpBlobStoreAdapter(fake_store)

        # Act
        result = await adapter.exists("some-hash")

        # Assert — sentinel is NOT forwarded as a wire BlobRef
        assert result is None


# ---------------------------------------------------------------------------
# T5 — Error translation: roxabi_blobs.BlobNotFoundError →
#       roxabi_contracts.BlobNotFoundError
# ---------------------------------------------------------------------------


class TestHttpBlobStoreAdapterErrorTranslation:
    async def test_get_translates_storage_blob_not_found_to_contracts_error(
        self,
    ) -> None:
        """get() translates storage BlobNotFoundError to contracts BlobNotFoundError.

        This asserts the storage→contracts error translation at the adapter seam:
        the adapter catches roxabi_blobs.BlobNotFoundError and re-raises the port-level
        roxabi_contracts.BlobNotFoundError so callers never need to import roxabi_blobs.
        Deleting the translation layer makes this test fail because
        pytest.raises(roxabi_contracts.BlobNotFoundError) won't catch the raw
        roxabi_blobs.BlobNotFoundError.
        """
        # Arrange
        import roxabi_blobs
        import roxabi_contracts
        from lyra.infrastructure.blobstore_adapter import HttpBlobStoreAdapter

        fake_store = _make_fake_http_store()
        cast(AsyncMock, fake_store.get).side_effect = roxabi_blobs.BlobNotFoundError(
            "k"
        )
        adapter = HttpBlobStoreAdapter(fake_store)

        # Act / Assert — translated error type is raised
        with pytest.raises(roxabi_contracts.BlobNotFoundError) as exc_info:
            await adapter.get("k")

        raised = exc_info.value
        # Assert on type and key — not on str(err) which may be redacted
        assert isinstance(raised, roxabi_contracts.BlobNotFoundError)
        assert raised.key == "k"
        # The raised exception must NOT be the storage-layer type
        assert not isinstance(raised, roxabi_blobs.BlobNotFoundError), (
            "adapter must translate roxabi_blobs.BlobNotFoundError into "
            "roxabi_contracts.BlobNotFoundError, not re-raise the storage type"
        )


# ---------------------------------------------------------------------------
# T6 — 5xx translation: httpx.HTTPStatusError ≥500 → BlobStoreServerError
# ---------------------------------------------------------------------------


class TestHttpBlobStoreAdapterServerErrorTranslation:
    async def test_put_translates_5xx_to_blob_store_server_error(self) -> None:
        """put() wraps httpx.HTTPStatusError with status ≥500 as BlobStoreServerError.

        Negative guard: removing the 5xx catch in HttpBlobStoreAdapter.put makes
        pytest.raises(roxabi_contracts.BlobStoreServerError) fail because the raw
        httpx.HTTPStatusError propagates uncaught.
        """
        # Arrange
        import httpx

        import roxabi_contracts
        from lyra.infrastructure.blobstore_adapter import HttpBlobStoreAdapter

        fake_store = _make_fake_http_store()
        # Build a minimal httpx.HTTPStatusError for a 503 response
        request = httpx.Request("PUT", "http://blobstore/blobs")
        response = httpx.Response(503, request=request)
        cast(AsyncMock, fake_store.put).side_effect = httpx.HTTPStatusError(
            "503 Service Unavailable", request=request, response=response
        )
        adapter = HttpBlobStoreAdapter(fake_store)

        # Act / Assert
        with pytest.raises(roxabi_contracts.BlobStoreServerError) as exc_info:
            await adapter.put(b"data", mime="image/png", source="discord")

        raised = exc_info.value
        assert raised.status_code == 503
        assert isinstance(raised, roxabi_contracts.BlobStoreServerError)

    async def test_put_does_not_wrap_4xx_as_blob_store_server_error(self) -> None:
        """put() does not wrap non-5xx HTTPStatusError as BlobStoreServerError.

        A 4xx (e.g. 400 Bad Request) must propagate as the raw httpx error,
        not be swallowed or re-typed as BlobStoreServerError.
        """
        # Arrange
        import httpx

        import roxabi_contracts
        from lyra.infrastructure.blobstore_adapter import HttpBlobStoreAdapter

        fake_store = _make_fake_http_store()
        request = httpx.Request("PUT", "http://blobstore/blobs")
        response = httpx.Response(400, request=request)
        cast(AsyncMock, fake_store.put).side_effect = httpx.HTTPStatusError(
            "400 Bad Request", request=request, response=response
        )
        adapter = HttpBlobStoreAdapter(fake_store)

        # Act / Assert — 4xx passes through as raw httpx.HTTPStatusError
        try:
            await adapter.put(b"data", mime="image/png", source="discord")
        except roxabi_contracts.BlobStoreServerError:
            pytest.fail("4xx must not be wrapped as BlobStoreServerError")
        except httpx.HTTPStatusError:
            pass  # expected — raw 4xx propagates unchanged


# ---------------------------------------------------------------------------
# T4 — Protocol conformance: HttpBlobStoreAdapter satisfies BlobStorePort
# ---------------------------------------------------------------------------


class TestHttpBlobStoreAdapterProtocolConformance:
    def test_adapter_satisfies_blobstore_port(self) -> None:
        """HttpBlobStoreAdapter structurally satisfies BlobStorePort.

        Negative guard: if put/get/exists is removed from the adapter,
        isinstance() will return False and this test fails.
        """
        # Arrange
        from lyra.core.ports.blobstore import BlobStorePort
        from lyra.infrastructure.blobstore_adapter import HttpBlobStoreAdapter

        fake_store = _make_fake_http_store()
        adapter = HttpBlobStoreAdapter(fake_store)

        # Assert
        assert isinstance(adapter, BlobStorePort) is True
