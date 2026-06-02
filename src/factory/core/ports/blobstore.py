"""BlobStorePort — driven port for content-addressed blob storage.

Pure Protocol: stdlib + roxabi_contracts only.
No roxabi_blobs, factory.infrastructure, or adapter imports permitted here.

See ADR-082 (BlobStorePort driven-port) and ADR-067 (BlobStore abstraction).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from roxabi_contracts import BlobRef


@runtime_checkable
class BlobStorePort(Protocol):
    """Driven (secondary) port for content-addressed blob storage.

    Implementations live in ``factory.infrastructure`` (e.g.
    ``HttpBlobStoreAdapter``).  The domain and inbound pipeline depend on
    this Protocol only — they never import ``roxabi_blobs`` or any HTTP
    client directly.

    Return type is always the WIRE ``roxabi_contracts.BlobRef`` (not the
    storage ``roxabi_blobs.BlobRef``).  The adapter layer is the sole seam
    responsible for the storage→wire conversion via
    ``BlobRef.from_store_ref``.

    ADR-082 + ADR-067.
    """

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
        """Store *data* and return a fully-populated wire BlobRef.

        The returned ``store_key`` is opaque — callers MUST NOT apply a
        ``sha256:`` regex or any other format assumption.  An empty
        ``store_key`` from a live implementation is a contract violation
        and the adapter raises ``ValueError`` immediately.
        """
        ...

    async def get(self, store_key: str) -> bytes:
        """Retrieve raw bytes by opaque *store_key*.

        Raises ``BlobNotFoundError`` from ``roxabi_contracts`` (NOT
        ``roxabi_blobs``) when the key is absent.  The adapter seam
        (``HttpBlobStoreAdapter``) translates the storage-layer error so
        callers only ever see the contracts type::

            from roxabi_contracts import BlobNotFoundError
            try:
                data = await blobstore.get(store_key)
            except BlobNotFoundError:
                ...

        *store_key* is server-issued and opaque — callers must not apply a
        ``sha256:`` regex or any other format assumption.  The BlobStore
        **server** enforces path containment via ``FsBlobStore._safe_resolve_in_root``
        (directory traversal attempts yield 404, not an oracle response).

        ``delete`` is intentionally absent from ``BlobStorePort``.  If a
        future delete capability is added it MUST raise
        ``roxabi_contracts.BlobNotFoundError`` on a missing key — same
        contract as ``get`` — so callers keep a single error type to catch.
        """
        ...

    async def exists(self, content_hash: str) -> BlobRef | None:
        """Return a wire BlobRef if *content_hash* is already stored, else None.

        The returned BlobRef is fully populated (non-sentinel).  Callers
        that only need existence can test truthiness; callers needing the
        full envelope should prefer ``put`` (idempotent via content-hash
        dedup).
        """
        ...


__all__ = ["BlobStorePort"]
