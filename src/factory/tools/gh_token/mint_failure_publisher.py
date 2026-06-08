"""Best-effort NATS publisher for GitHub token-mint failures.

Publishes a MintFailureEvent on ``factory.gh.mint_failure.<machine>`` whenever
a MintError is raised. Publication is fire-and-forget: any NATS error is
logged and swallowed so that a NATS outage never breaks or stalls token minting.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import uuid4

from factory.tools.gh_token.helper import MintError
from roxabi_contracts.envelope import CONTRACT_VERSION
from roxabi_contracts.gh.models import MintFailureEvent
from roxabi_contracts.gh.subjects import gh_mint_failure

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS

log = logging.getLogger(__name__)


def _reason_label(exc: MintError) -> str:
    """Map a MintError to a short failure label for the event ``reason`` field."""
    if exc.http_status is not None:
        return f"github_api_{exc.http_status}"
    return "network_error"


class MintFailurePublisher:
    """Best-effort NATS publisher for MintFailureEvent.

    Wraps a NATS client and a machine identifier. ``publish()`` never raises —
    NATS errors are logged at WARNING level and swallowed so that callers can
    fire-and-forget without disrupting the token-minting critical path.

    ``__init__`` validates ``machine`` (single subject segment, no dots) and
    precomputes the publish subject, so an invalid machine fails fast at
    construction rather than being silently swallowed by ``publish()``. This
    protects every caller, not just the daemon path that pre-sanitizes via
    ``_safe_machine_name`` (#1708).
    """

    def __init__(self, nc: "NATS", machine: str) -> None:
        self._nc = nc
        self._machine = machine
        # Validate + precompute subject here so a bad machine raises ValueError
        # at construction instead of being swallowed by the best-effort publish.
        self._subject = gh_mint_failure(machine)

    async def publish(self, exc: MintError) -> None:
        """Publish a MintFailureEvent for *exc*. Best-effort — never raises."""
        try:
            event = MintFailureEvent(
                contract_version=CONTRACT_VERSION,
                trace_id=str(uuid4()),
                issued_at=datetime.now(timezone.utc),
                machine=self._machine,
                reason=_reason_label(exc),
                http_status=exc.http_status,
                retries=exc.retries,
            )
            await self._nc.publish(
                self._subject, event.model_dump_json().encode("utf-8")
            )
        except Exception as pub_exc:  # noqa: BLE001 — best-effort publish; must not break token minting
            log.warning("mint_failure_publisher: publish failed: %s", pub_exc)
