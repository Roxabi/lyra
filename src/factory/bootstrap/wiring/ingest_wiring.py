"""Ingest wiring helpers for bootstrap (#1551, ADR-083)."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from factory.core.ports.blobstore import BlobStorePort

from factory.inbound.attachment_ingest import AttachmentIngestStage, IngestCtx


def build_ingest(
    blob_store: "BlobStorePort | None",
) -> tuple[IngestCtx, AttachmentIngestStage | None]:
    """Compose the inbound ingest context + stage from an optional blob store.

    store=None (CLI / degraded / blobstore unconfigured) ->
    (IngestCtx(store=None), None) so the inbound pipeline's ingest guard no-ops.
    store present -> a live IngestCtx
    plus a (stateless) AttachmentIngestStage. (#1551, ADR-083.)
    """
    stage = AttachmentIngestStage() if blob_store is not None else None
    return IngestCtx(store=blob_store), stage


def _assert_blobstore_configured_if_url_set(ingest: IngestCtx) -> None:
    """Fail fast on misconfiguration: URL set but the store failed to initialise.

    Honors #1540 graceful degradation: when ``FACTORY_BLOBSTORE_URL`` is unset
    (dev / CLI), a ``None`` store is expected and we degrade silently. When the
    URL IS set but ``init_blobstore()`` returned ``None`` (token file absent or
    unreadable -> misconfiguration), raise so inbound attachment ingest cannot
    silently no-op in production. (#1551 S8, reconciles #1540.)
    """
    url = os.environ.get("FACTORY_BLOBSTORE_URL")
    if url and ingest.store is None:
        raise RuntimeError(
            f"FACTORY_BLOBSTORE_URL={url!r} is set but BlobStore failed to "
            "initialise (FACTORY_BLOBSTORE_TOKEN_PATH absent or unreadable) — "
            "inbound attachment ingest would silently no-op."
        )


def wire_ingest(adapter: Any, blob_store: "BlobStorePort | None") -> None:
    """Inject the ingest context onto an adapter + enforce the prod invariant.

    Single seam called from EVERY bootstrap path (standalone + hub) so no path
    can omit ingest wiring (#1551 S8, ADR-083). The live stage runs in the
    module-level inbound pipeline; bootstrap only supplies the IngestCtx (store)
    that the pipeline reads at call time, so the returned stage is unused here.
    """
    ingest_ctx, _stage = build_ingest(blob_store)
    adapter._ingest_ctx = ingest_ctx
    _assert_blobstore_configured_if_url_set(ingest_ctx)
