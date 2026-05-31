"""Central inbound attachment ingest stage (ADR-083, epic #1537).

Voice path (#1551): fetches a pending attachment via its closure, stores the
bytes in BlobStore, and stamps the returned real BlobRef onto
``InboundMessage.audio.blob_ref``.  Non-audio attachments (P2, #1552) are
passed through with ``pending_attachment`` cleared.

Layer invariant: this module MUST NOT import ``discord``, ``aiogram``, or
``lyra.adapters``.  BlobStorePort is imported from ``lyra.core.ports.blobstore``
(core-only driven port).  PendingAttachment holds the fetch closure supplied by
the adapter layer; the core message carries it as ``Any`` to avoid a
core→inbound circular import (see ``InboundMessage.pending_attachment``).
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from lyra.core.messaging.message import InboundMessage
    from lyra.core.ports.blobstore import BlobStorePort

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


@dataclass(frozen=True)
class IngestCtx:
    """Context for the attachment ingest stage."""

    store: "BlobStorePort | None"


class AttachmentIngestStage:
    """Central inbound attachment ingest (ADR-083, epic #1537). Voice path (#1551)."""

    async def run(self, msg: "InboundMessage", ctx: IngestCtx) -> "InboundMessage":
        """Resolve a pending attachment and stamp the real BlobRef onto the message.

        Contract:
        - No pending_attachment or no store → no-op passthrough (msg returned
          unchanged, including any PENDING sentinel on audio.blob_ref).
        - Fetch failure → degraded: ``pending_attachment`` cleared, audio
          blob_ref preserved as-is (still PENDING — downstream STT will degrade
          gracefully).
        - Success (audio path) → real BlobRef stamped on ``msg.audio.blob_ref``;
          ``pending_attachment`` cleared.
        - Success (non-audio path, P2 #1552) → ``pending_attachment`` cleared;
          audio untouched.

        CRITICAL: both degraded and success paths set ``pending_attachment=None``
        before returning.  The NATS serialiser recurses into dataclasses and
        would encounter the fetch closure, producing an undecodable dict.
        """
        pending = cast("PendingAttachment | None", msg.pending_attachment)
        if pending is None or ctx.store is None:
            return msg  # no-op passthrough

        try:
            data = await pending.fetch()
            wire_ref = await ctx.store.put(
                data,
                mime=pending.mime,
                source=pending.source,
                filename=pending.filename,
                platform_ref=pending.platform_ref,
                platform_message_id=pending.platform_message_id,
            )
        except Exception:
            log.exception("attachment ingest failed — degraded (PENDING preserved)")
            return dataclasses.replace(msg, pending_attachment=None)

        if msg.audio is None:
            # non-audio = P2 (#1552)
            return dataclasses.replace(msg, pending_attachment=None)
        new_audio = dataclasses.replace(msg.audio, blob_ref=wire_ref)
        return dataclasses.replace(msg, audio=new_audio, pending_attachment=None)
