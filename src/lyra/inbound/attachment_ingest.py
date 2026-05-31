"""Central inbound attachment ingest stage (ADR-083, epic #1537).

Voice path (#1551): fetches a pending attachment via its closure, stores the
bytes in BlobStore, and stamps the returned real BlobRef onto
``InboundMessage.audio.blob_ref``.

Non-audio path (#1552): iterates ``pending_attachments``, enforces a per-item
size cap before fetching, stores each item, stamps the returned real BlobRef
onto the matching ``InboundMessage.attachments[i].blob_ref``, and clears
``pending_attachments`` so the message is safe to serialise over NATS.

Layer invariant: this module MUST NOT import ``discord``, ``aiogram``, or
``lyra.adapters``.  BlobStorePort is imported from ``lyra.core.ports.blobstore``
(core-only driven port).  PendingAttachment holds the fetch closure supplied by
the adapter layer; the core message carries it as ``Any`` to avoid a
core→inbound circular import (see ``InboundMessage.pending_attachment``).
"""

from __future__ import annotations

import dataclasses
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from roxabi_contracts import BlobStoreServerError

if TYPE_CHECKING:
    from lyra.core.messaging.message import InboundMessage
    from lyra.core.ports.blobstore import BlobStorePort

# Maximum bytes for a non-audio attachment before download is refused.
# Mirrors LYRA_MAX_AUDIO_BYTES (TG getFile 20 MiB cap) — raised via env for
# large CDN→PUT transfers (#1552 / parent Open-Q1).
_raw_max_ingest = os.environ.get("LYRA_MAX_ATTACHMENT_INGEST_BYTES")
try:
    MAX_ATTACHMENT_INGEST_BYTES: int = (
        int(_raw_max_ingest) if _raw_max_ingest else 20 * 1024 * 1024
    )
except ValueError:
    raise ValueError(
        f"LYRA_MAX_ATTACHMENT_INGEST_BYTES must be an int, got {_raw_max_ingest!r}"
    ) from None

FetchFn = Callable[[], Awaitable[bytes]]
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PendingAttachment:
    """Transient descriptor set by adapters to carry a deferred fetch closure.

    Adapters construct this just before calling the inbound pipeline; the
    stage resolves it to a real BlobRef and clears the field so the message
    is safe to serialise over NATS (fetch closures cannot cross process
    boundaries).
    """

    fetch: FetchFn
    mime: str
    source: str
    platform_ref: str | None = None
    platform_message_id: str | None = None
    filename: str | None = None
    # Declared byte count supplied by the platform before download; used for
    # the oversize guard in _ingest_non_audio (#1552).
    size: int | None = None


class AttachmentIngestError(Exception):
    """Non-audio ingest failed (oversize before download, or BlobStore 5xx).

    Carries a user-facing message the adapter boundary surfaces as a reply (#1552).
    """

    def __init__(self, user_message: str) -> None:
        self.user_message = user_message
        super().__init__(user_message)


@dataclass(frozen=True)
class IngestCtx:
    """Context for the attachment ingest stage."""

    store: "BlobStorePort | None"


class AttachmentIngestStage:
    """Central inbound attachment ingest (ADR-083, epic #1537).

    Handles both carriers:
    - Voice / audio path (#1551): singular ``pending_attachment`` → stamps
      ``msg.audio.blob_ref`` with the real BlobRef; degrades gracefully on error.
    - Non-audio path (#1552): list ``pending_attachments`` → stamps each
      ``msg.attachments[i].blob_ref``; clears ``pending_attachments``; raises
      ``AttachmentIngestError`` on oversize or BlobStore 5xx.
    """

    async def run(self, msg: "InboundMessage", ctx: IngestCtx) -> "InboundMessage":
        """Resolve pending attachments and stamp real BlobRefs onto the message.

        Contract:
        - ``ctx.store is None`` → no-op passthrough (CLI/test; leaves carriers
          intact, unchanged contract).
        - Non-audio path (``msg.pending_attachments`` non-empty):
          each item size-checked before fetch; on success stamps
          ``attachments[i].blob_ref``; clears ``pending_attachments``.
          Raises ``AttachmentIngestError`` on oversize or BlobStore 5xx.
        - Audio path (``msg.pending_attachment`` set):
          fetch failure or put failure → degraded: ``pending_attachment``
          cleared, ``audio.blob_ref`` set to None.
          Success → real BlobRef stamped on ``msg.audio.blob_ref``;
          ``pending_attachment`` cleared.

        CRITICAL: both degraded and success audio paths set
        ``pending_attachment=None`` before returning.  The NATS serialiser
        recurses into dataclasses and would encounter the fetch closure,
        producing an undecodable dict.

        AXIAL GUARD: ``source`` is never inspected to branch logic — it is
        only forwarded to ``store.put``.
        """
        if ctx.store is None:
            return msg  # no-op passthrough (CLI/test; leaves carriers intact)

        if msg.pending_attachments:  # non-audio (P2 #1552)
            msg = await self._ingest_non_audio(msg, ctx.store)

        pending = cast("PendingAttachment | None", msg.pending_attachment)
        if pending is not None:  # audio (#1551) — UNCHANGED policy
            msg = await self._ingest_audio(msg, pending, ctx.store)

        return msg

    async def _ingest_audio(
        self,
        msg: "InboundMessage",
        pending: "PendingAttachment",
        store: "BlobStorePort",
    ) -> "InboundMessage":
        """Voice ingest: fetch → store.put → stamp audio.blob_ref.

        Degrades gracefully on any error: blob_ref set to None, closure cleared.
        The STT middleware detects blob_ref=None and drops the message with a
        user-facing error reply.
        """
        try:
            data = await pending.fetch()
            wire_ref = await store.put(
                data,
                mime=pending.mime,
                source=pending.source,
                filename=pending.filename,
                platform_ref=pending.platform_ref,
                platform_message_id=pending.platform_message_id,
            )
        except Exception:
            log.exception("attachment ingest failed — degraded (blob_ref=None)")
            if msg.audio is not None:
                new_audio = dataclasses.replace(msg.audio, blob_ref=None)
                return dataclasses.replace(
                    msg, audio=new_audio, pending_attachment=None
                )
            return dataclasses.replace(msg, pending_attachment=None)

        if msg.audio is None:
            # Non-audio message that somehow had a singular pending_attachment;
            # clear the closure without touching attachments.
            return dataclasses.replace(msg, pending_attachment=None)
        new_audio = dataclasses.replace(msg.audio, blob_ref=wire_ref)
        return dataclasses.replace(msg, audio=new_audio, pending_attachment=None)

    async def _ingest_non_audio(
        self,
        msg: "InboundMessage",
        store: "BlobStorePort",
    ) -> "InboundMessage":
        """Non-audio ingest: size-guard → fetch → store.put.

        Stamps each ``attachments[i].blob_ref`` with the returned BlobRef.
        Raises ``AttachmentIngestError`` on oversize or BlobStore 5xx.
        """
        new_atts = list(msg.attachments)
        pending_attachments = cast("list[PendingAttachment]", msg.pending_attachments)
        for i, p in enumerate(pending_attachments):
            if p.size is not None and p.size > MAX_ATTACHMENT_INGEST_BYTES:
                log.warning(
                    "attachment too large: %s > %s (source=%s)",
                    p.size,
                    MAX_ATTACHMENT_INGEST_BYTES,
                    p.source,
                )
                raise AttachmentIngestError("That file is too large to process.")
            data = await p.fetch()
            if len(data) > MAX_ATTACHMENT_INGEST_BYTES:
                log.warning(
                    "attachment exceeded cap after fetch: %s > %s (source=%s)",
                    len(data),
                    MAX_ATTACHMENT_INGEST_BYTES,
                    p.source,
                )
                raise AttachmentIngestError("That file is too large to process.")
            try:
                ref = await store.put(
                    data,
                    mime=p.mime,
                    source=p.source,
                    filename=p.filename,
                    platform_ref=p.platform_ref,
                    platform_message_id=p.platform_message_id,
                )
            except BlobStoreServerError:
                log.exception("blobstore rejected attachment (source=%s)", p.source)
                raise AttachmentIngestError(
                    "Couldn't store your attachment — please try again."
                ) from None
            if i < len(new_atts):
                new_atts[i] = dataclasses.replace(new_atts[i], blob_ref=ref)
        return dataclasses.replace(msg, attachments=new_atts, pending_attachments=[])
