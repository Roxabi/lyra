"""NatsWorkerClientBase — shared heartbeat lifecycle for hub-side NATS worker clients.

Lyra-local base class (inherits ``NatsDriverBase``) that bundles the common
heartbeat subscription lifecycle used by the 4 hub-side worker clients
(LLM, TTS, STT, Image). Subclasses set 3 class attrs to specialize:

- ``HB_SUBJECT``         — NATS subject to subscribe for heartbeats (from parent).
- ``LOG_PREFIX``         — string prepended to log messages (e.g. ``"llm_client:"``).
- ``VALIDATE_WORKER_ID`` — callable that raises ``ValueError`` on unsafe worker IDs.

The override of ``_on_heartbeat`` co-populates both ``_registry`` (rich-payload
``WorkerRegistry``) and ``_worker_freshness`` (parent's timestamp dict) so that
``_any_worker_alive()`` — used by the streaming backstop in ``_wait_for_chunk`` —
keeps working without modification.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from lyra.nats.worker_registry import WorkerRegistry
from roxabi_nats.circuit_breaker import NatsCircuitBreaker
from roxabi_nats.driver_base import NatsDriverBase

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS

__all__ = ["NatsWorkerClientBase"]
log = logging.getLogger(__name__)


def _noop_validate_worker_id(worker_id: str) -> None:
    """Default ``VALIDATE_WORKER_ID`` — accept anything (subclasses override)."""
    del worker_id


class NatsWorkerClientBase(NatsDriverBase):
    """Lyra-local base for hub-side NATS worker clients.

    Adds a ``WorkerRegistry`` + ``NatsCircuitBreaker`` and overrides
    ``_on_heartbeat`` to parse rich heartbeat payloads, validate the
    ``worker_id``, and feed both the registry and the parent's freshness
    dict.

    Subclasses must set ``HB_SUBJECT`` and ``LOG_PREFIX``. They may set
    ``VALIDATE_WORKER_ID`` to their domain re-export of
    ``validate_worker_id`` (from ``roxabi_contracts.<domain>``). The default
    is a no-op; the ``WorkerRegistry`` applies its own secondary guard via
    ``validate_nats_token``.
    """

    # Inherited from NatsDriverBase: HB_SUBJECT: str = ""
    LOG_PREFIX: str = ""
    # Callable that raises ValueError for unsafe worker_id values. Subclasses
    # assign their domain-specific re-export of validate_worker_id. The
    # staticmethod wrapper lets subclasses set it as a plain class attr
    # (``VALIDATE_WORKER_ID = validate_worker_id``) without Python treating the
    # function as an unbound method.
    VALIDATE_WORKER_ID: Callable[[str], None] = staticmethod(_noop_validate_worker_id)

    def __init__(
        self,
        nc: "NATS",
        *,
        timeout: float = 120.0,
        max_total_duration: float | None = None,
    ) -> None:
        super().__init__(nc, timeout=timeout, max_total_duration=max_total_duration)
        self._registry: WorkerRegistry = WorkerRegistry()
        self._cb: NatsCircuitBreaker = NatsCircuitBreaker()

    # ── Heartbeat override ────────────────────────────────────────────────────

    async def _on_heartbeat(self, msg: Any) -> None:
        """Parse heartbeat, validate worker_id, feed registry + freshness dict."""
        try:
            data: dict = json.loads(msg.data)
        except (json.JSONDecodeError, ValueError):
            log.warning("%s heartbeat JSON decode error, ignoring", self.LOG_PREFIX)
            return

        worker_id = data.get("worker_id")

        # Reject None, empty string, and non-string types.
        if not isinstance(worker_id, str) or not worker_id:
            log.warning(
                "%s heartbeat missing or invalid worker_id=%r, ignoring",
                self.LOG_PREFIX,
                worker_id,
            )
            return

        # Domain-specific validation (e.g. NATS-subject-safe chars). The
        # WorkerRegistry applies a second independent guard (validate_nats_token)
        # — keep both for defense in depth.
        try:
            type(self).VALIDATE_WORKER_ID(worker_id)
        except ValueError:
            log.warning(
                "%s heartbeat with unsafe worker_id=%r, ignoring",
                self.LOG_PREFIX,
                worker_id,
            )
            return

        # Co-populate both sinks:
        # 1. Rich-payload registry (score-based routing, any_alive()).
        self._registry.record_heartbeat(data)
        # 2. Parent's freshness dict (_any_worker_alive() streaming backstop).
        self._worker_freshness[worker_id] = time.monotonic()

    # ── Public helpers ────────────────────────────────────────────────────────

    def any_alive(self) -> bool:
        """Return True if at least one worker has sent a recent heartbeat."""
        return self._registry.any_alive()
