"""BlobAuditSink — publishes BlobAuditEvent to NATS JetStream lyra.audit.blobs.<op>."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import nats.errors

from roxabi_contracts.audit.blobs import BlobAuditEvent

if TYPE_CHECKING:
    from nats.js.client import JetStreamContext

log = logging.getLogger(__name__)


class BlobAuditSink:
    """Emit BlobAuditEvent to NATS JetStream; falls back to logger when degraded."""

    def __init__(self) -> None:
        self._js: JetStreamContext | None = None
        self._degraded: bool = False
        self._security_log = logging.getLogger("lyra.security")

    async def emit(self, event: BlobAuditEvent) -> None:
        """Publish event; never raises — falls back to lyra.security logger on error."""
        json_str = event.model_dump_json()
        payload = json_str.encode()
        subject = BlobAuditEvent.subject_for(event.op)

        if self._degraded or self._js is None:
            self._security_log.warning("AUDIT DEGRADED [%s]: %s", subject, json_str)
            return

        try:
            await self._js.publish(subject, payload)
        except nats.errors.Error as exc:
            log.error(
                "AUDIT: emit failed (%s) on subject %s — marking degraded",
                type(exc).__name__,
                subject,
            )
            self._degraded = True
            self._security_log.warning(
                "AUDIT DEGRADED [%s]: %s", subject, json_str
            )
