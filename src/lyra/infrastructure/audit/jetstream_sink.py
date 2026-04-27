"""JetStreamAuditSink — publishes SecurityEvent to NATS JetStream LYRA_AUDIT stream."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from roxabi_contracts.audit import SecurityEvent

if TYPE_CHECKING:
    from nats.aio.client import Client as NatsClient
    from nats.js.client import JetStreamContext

log = logging.getLogger(__name__)
_security_log = logging.getLogger("lyra.security")

_SUBJECT_PRIVILEGED = "lyra.audit.security.privileged"
_SUBJECT_NORMAL = "lyra.audit.security.normal"


class JetStreamAuditSink:
    """Emit SecurityEvent to NATS JetStream; falls back to logger when degraded.

    Lifecycle::

        sink = JetStreamAuditSink()
        await sink.provision(nc)   # at bootstrap, after NATS connect
        # ... runtime ...
        await sink.emit(event)     # called via asyncio.create_task() in _spawn()
    """

    def __init__(self) -> None:
        self._js: JetStreamContext | None = None
        self._degraded: bool = False

    async def provision(self, nc: NatsClient) -> None:
        """Create or update LYRA_AUDIT stream; mark degraded if unavailable."""
        from nats.js.api import RetentionPolicy, StorageType, StreamConfig
        from nats.js.errors import BadRequestError

        try:
            js = nc.jetstream()
        except Exception as exc:
            log.warning(
                "AUDIT: JetStream not available — emitting to lyra.security logger: %s",
                exc,
            )
            self._degraded = True
            return

        cfg = StreamConfig(
            name="LYRA_AUDIT",
            subjects=["lyra.audit.>"],
            retention=RetentionPolicy.LIMITS,
            storage=StorageType.FILE,
            max_age=90 * 86400,
            max_bytes=1 * 1024**3,
            duplicate_window=60,  # seconds — nats-py converts to ns internally
        )
        try:
            await js.add_stream(cfg)
        except BadRequestError:
            try:
                await js.update_stream(cfg)
            except Exception as exc:
                log.warning("AUDIT: stream config mismatch — marking degraded: %s", exc)
                self._degraded = True
                return
        except Exception as exc:
            log.warning(
                "AUDIT: JetStream not available — emitting to lyra.security logger: %s",
                exc,
            )
            self._degraded = True
            return

        self._js = js

    async def emit(self, event: SecurityEvent) -> None:
        """Publish event; never raises — falls back to lyra.security logger on error."""
        json_str = event.model_dump_json()
        payload = json_str.encode()
        try:
            if self._degraded or self._js is None:
                _security_log.warning("%s", json_str)
                return
            subject = _SUBJECT_PRIVILEGED if event.skip_permissions else _SUBJECT_NORMAL
            await self._js.publish(subject, payload)
        except Exception as exc:
            _security_log.warning(
                "AUDIT: emit failed (%s) — %s", type(exc).__name__, json_str
            )
