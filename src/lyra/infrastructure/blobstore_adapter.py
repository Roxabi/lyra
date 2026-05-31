"""HttpBlobStoreAdapter — infrastructure adapter satisfying BlobStorePort.

Wraps a ``roxabi_blobs.HttpBlobStore`` (injected) and converts the storage
``roxabi_blobs.BlobRef`` to the wire ``roxabi_contracts.BlobRef`` via the
canonical ``BlobRef.from_store_ref`` converter.

This module is the single seam permitted to import both ``roxabi_blobs``
(storage layer) and ``roxabi_contracts`` (wire layer).  All other lyra
modules depend on ``BlobStorePort`` only.

See ADR-082 (driven-port) and ADR-067 (BlobStore abstraction).
"""

from __future__ import annotations

import httpx

import roxabi_blobs
from roxabi_blobs import HttpBlobStore
from roxabi_blobs.models import BlobRef as StorageBlobRef
from roxabi_contracts import (
    BlobNotFoundError,
    BlobRef,
    BlobStoreServerError,
)


class HttpBlobStoreAdapter:
    """Adapter implementing ``BlobStorePort`` via structural subtyping.

    Constructor takes an already-constructed ``HttpBlobStore`` so the
    adapter is independently testable — callers inject a mock or an
    in-process ASGI-backed store without any HTTP server.
    """

    def __init__(self, http_store: HttpBlobStore) -> None:
        self._http_store = http_store

    async def put(  # noqa: PLR0913 — signature locked by ADR-067 §Interface
        self,
        data: bytes,
        *,
        mime: str,
        source: str,
        filename: str | None = None,
        platform_ref: str | None = None,
        platform_message_id: str | None = None,
    ) -> BlobRef:
        """Delegate to the wrapped store; convert STORAGE→WIRE; guard empty key.

        ``BlobRef.from_store_ref`` is the only conversion path — no manual
        BlobRef construction.  An empty ``store_key`` after a real ingest
        indicates a storage contract violation and raises ``ValueError``
        immediately (store_key is opaque — no format assumption).
        """
        try:
            storage_ref: StorageBlobRef = await self._http_store.put(
                data,
                mime=mime,
                source=source,
                filename=filename,
                platform_ref=platform_ref,
                platform_message_id=platform_message_id,
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500:
                raise BlobStoreServerError(
                    f"BlobStore returned HTTP {e.response.status_code}",
                    status_code=e.response.status_code,
                ) from e
            raise
        except httpx.RequestError as e:
            raise BlobStoreServerError(
                f"BlobStore request failed: {type(e).__name__}",
                status_code=503,
            ) from e
        wire = BlobRef.from_store_ref(storage_ref)
        if not wire.store_key:
            raise ValueError(
                "BlobStore.put returned an empty store_key; "
                "a live ingest must yield a real store_key"
            )
        return wire

    async def get(self, store_key: str) -> bytes:
        """Retrieve raw bytes by opaque store_key; translates storage error.

        Catches ``roxabi_blobs.BlobNotFoundError`` at the seam and re-raises
        it as ``roxabi_contracts.BlobNotFoundError`` so callers never see the
        storage-layer type.
        """
        try:
            return await self._http_store.get(store_key)
        except roxabi_blobs.BlobNotFoundError as e:
            raise BlobNotFoundError(e.args[0] if e.args else str(e)) from e

    async def aclose(self) -> None:
        """Close the underlying httpx client (resource cleanup at shutdown).

        Mirrors ``HttpBlobStore.__aexit__``: closes the lazy client if it was
        ever created, then stops any ASGI lifespan task (test seam only).
        Safe to call when the client was never used (no-op).
        """
        if self._http_store._client is not None:
            await self._http_store._client.aclose()
            self._http_store._client = None
        await self._http_store._stop_asgi_lifespan()

    async def exists(self, content_hash: str) -> BlobRef | None:
        """Return a wire BlobRef if the blob exists, else None.

        ``HttpBlobStore.exists`` returns a sparse sentinel (``is_sentinel=True``,
        ``content_hash=""``) on a HEAD hit.  That sentinel cannot be forwarded
        through ``from_store_ref`` because the wire BlobRef requires a non-empty
        ``content_hash`` (see http_store.py docstring warning).  Sentinels are
        therefore mapped to ``None`` here — callers that need the full envelope
        should ``put`` (content-hash dedup makes it idempotent).
        """
        storage_ref: StorageBlobRef | None = await self._http_store.exists(content_hash)
        if storage_ref is None:
            return None
        if storage_ref.is_sentinel:
            # Sentinel: existence confirmed but full envelope unavailable over HEAD.
            # Cannot produce a valid wire BlobRef — return None (treat as not found
            # for wire purposes). Callers needing the full envelope must PUT.
            return None
        return BlobRef.from_store_ref(storage_ref)


__all__ = ["HttpBlobStoreAdapter"]
