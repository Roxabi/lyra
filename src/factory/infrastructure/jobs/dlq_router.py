"""DLQ router for FACTORY_JOBS.

Republishes exhausted jobs to factory.jobs.dlq.<domain>.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

import nats.errors

if TYPE_CHECKING:
    from nats.aio.msg import Msg
    from nats.js import JetStreamContext

log = logging.getLogger(__name__)

ADVISORY_SUBJECT = "$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.FACTORY_JOBS.>"
_STREAM_NAME = "FACTORY_JOBS"
_DLQ_PREFIX = "factory.jobs.dlq"
_HDR_ORIG_SUBJECT = "Roxabi-Dlq-Orig-Subject"
_HDR_DELIVERIES = "Roxabi-Dlq-Deliveries"
_HDR_STREAM_SEQ = "Roxabi-Dlq-Stream-Seq"


class DlqRouter:
    """Subscribe to MAX_DELIVERIES advisories and route exhausted jobs to DLQ subjects.

    Advisory → MSG.GET(stream_seq) → publish factory.jobs.dlq.<domain> → MSG.DELETE.
    The DLQ subject is itself captured by the FACTORY_JOBS WorkQueue stream
    (factory.jobs.dlq.> is enumerated in SUBJECTS — see stream_setup.py).
    """

    def __init__(self, nc: Any, js: "JetStreamContext") -> None:
        self._nc = nc
        self._js = js
        self._sub: Any = None

    async def start(self) -> None:
        """Subscribe to MAX_DELIVERIES advisories."""
        self._sub = await self._nc.subscribe(ADVISORY_SUBJECT, cb=self._handle)
        log.info("DlqRouter: subscribed to %s", ADVISORY_SUBJECT)

    async def stop(self) -> None:
        """Unsubscribe from MAX_DELIVERIES advisories."""
        if self._sub is not None:
            await self._sub.unsubscribe()
            self._sub = None
            log.info("DlqRouter: unsubscribed.")

    async def _handle(self, msg: "Msg") -> None:
        """Process a single MAX_DELIVERIES advisory.

        Advisory payload is JSON; extracts stream_seq, fetches the original message,
        re-publishes it to the DLQ subject, then deletes from the stream.
        """
        try:
            try:
                advisory = json.loads(msg.data)
            except (json.JSONDecodeError, ValueError):
                log.warning("DlqRouter: failed to parse advisory JSON — skipping")
                return

            if not isinstance(advisory, dict):
                log.warning("DlqRouter: advisory is not a JSON object — skipping")
                return

            stream_seq = advisory.get("stream_seq")
            if stream_seq is None:
                log.warning(
                    "DlqRouter: advisory missing stream_seq — skipping"
                    " (deliveries=%s)",
                    advisory.get("deliveries"),
                )
                return

            deliveries = advisory.get("deliveries", 0)

            jsm = self._nc.jsm()  # synchronous — no await (nats-py nc.jsm() is a plain def)

            try:
                raw = await jsm.get_msg(_STREAM_NAME, seq=stream_seq)
            except (nats.errors.Error, OSError):
                log.exception(
                    "DlqRouter: failed to fetch seq=%s from %s — skipping DLQ route",
                    stream_seq,
                    _STREAM_NAME,
                )
                return

            orig_subject: str = raw.subject or ""

            # Do not re-dead-letter a message already on the DLQ lane. A
            # factory.jobs.dlq.* subject that itself exhausts MAX_DELIVERIES must not
            # recurse into factory.jobs.dlq.dlq (captured by FACTORY_JOBS but with no
            # onward route → silent accumulation). Leave it in the stream to age out
            # via max_age; ops tooling owns DLQ-lane inspection.
            if orig_subject.startswith(f"{_DLQ_PREFIX}."):
                log.warning(
                    "DlqRouter: seq=%s already on DLQ lane (%r) — not re-routing",
                    stream_seq,
                    orig_subject,
                )
                return

            domain = _extract_domain(orig_subject)
            dlq_subject = f"{_DLQ_PREFIX}.{domain}"

            headers = {
                _HDR_ORIG_SUBJECT: orig_subject,
                _HDR_DELIVERIES: str(deliveries),  # int→str — safe per str_exc_bus_bound
                _HDR_STREAM_SEQ: str(stream_seq),  # int→str — safe per str_exc_bus_bound
            }

            try:
                await self._nc.publish(dlq_subject, raw.data or b"", headers=headers)
            except (nats.errors.Error, OSError):
                log.exception(
                    "DlqRouter: failed to publish to %s (seq=%s) — aborting DLQ route",
                    dlq_subject,
                    stream_seq,
                )
                return

            try:
                await jsm.delete_msg(_STREAM_NAME, stream_seq)
            except (nats.errors.Error, OSError):
                log.warning(
                    "DlqRouter: failed to delete seq=%s from %s"
                    " (DLQ msg published, seq not cleaned up)",
                    stream_seq,
                    _STREAM_NAME,
                )
                return

            log.info(
                "DlqRouter: routed seq=%s orig=%r → %s (deliveries=%s)",
                stream_seq,
                orig_subject,
                dlq_subject,
                deliveries,
            )
        except (nats.errors.Error, json.JSONDecodeError, KeyError, TypeError, ValueError):
            log.exception(
                "DlqRouter: unexpected error processing advisory — skipping"
            )
            return


def _extract_domain(subject: str) -> str:
    """Extract domain token from a factory.jobs.<domain>... subject.

    Returns ``"unknown"`` for any subject that does not match the expected pattern.
    """
    parts = subject.split(".")
    if len(parts) >= 3 and parts[0] == "factory" and parts[1] == "jobs":
        domain = parts[2]
        # Never re-target the DLQ lane onto itself (factory.jobs.dlq.dlq is
        # captured by FACTORY_JOBS but unroutable). _handle skips DLQ-lane
        # subjects upstream; this is defense-in-depth for direct callers.
        if domain == "dlq":
            return "unknown"
        return domain
    return "unknown"
