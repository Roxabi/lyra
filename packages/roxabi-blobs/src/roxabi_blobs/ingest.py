"""Shared eager-ingest helper — prevent N×M adapter duplication (ADR-073 three-strikes).

All adapters (Telegram #1065, Discord #1066, future Slack/CLI) call this single
function to ingest raw bytes into any `BlobStore`-conformant backend.
"""

from __future__ import annotations

from roxabi_blobs.models import BlobRef
from roxabi_blobs.protocol import BlobStore


async def ingest_bytes_to_blob_ref(  # noqa: PLR0913 — signature locked by issue #1333
    store: BlobStore,
    data: bytes,
    *,
    mime: str,
    source: str,
    platform_ref: str | None = None,
    platform_message_id: str | None = None,
    filename: str | None = None,
) -> BlobRef:
    """Ingest `data` into `store`, deduplicating by sha256.

    Returns a `BlobRef` whether or not the blob was already present.
    On a dedup hit the underlying file is NOT rewritten; a new `blob_refs`
    provenance row is still recorded for this ingestion event (ADR-067 §Interface).

    `source` is stored verbatim — no validation against a hardcoded enum so
    future adapters ("slack_audio", "cli_file", …) work without modifying
    this helper.
    """
    return await store.put(
        data,
        mime=mime,
        source=source,
        platform_ref=platform_ref,
        platform_message_id=platform_message_id,
        filename=filename,
    )
