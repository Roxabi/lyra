"""Circuit breaker mixin for Hub — split from hub.py (#760).

Provides circuit_breaker_drop(), record_circuit_success(), and
record_circuit_failure() for Hub.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from lyra.errors import ProviderError

if TYPE_CHECKING:
    from ..circuit_breaker import CircuitRegistry
    from ..messaging.message import InboundMessage, OutboundMessage, Response
    from ..messaging.messages import MessageManager

log = logging.getLogger(__name__)


class HubCircuitBreakerMixin:
    """Mixin providing circuit breaker integration for Hub."""

    # Declared for type-checking — initialised by Hub.__init__.
    if TYPE_CHECKING:
        circuit_registry: CircuitRegistry | None
        _msg_manager: MessageManager | None

        async def dispatch_response(
            self,
            msg: "InboundMessage",
            response: "Response | OutboundMessage",
        ) -> None: ...

    async def circuit_breaker_drop(self, msg: InboundMessage) -> bool:
        """Return True if the circuit is open and a fast-fail reply was sent."""
        if self.circuit_registry is None:
            return False
        cb = self.circuit_registry.get("claude-cli")
        if cb is None or not cb.is_open():
            return False
        status = cb.get_status()
        retry_secs = int(status.retry_after or 0)
        _retry_str = str(retry_secs)
        _unavail = (
            self._msg_manager.get("unavailable", retry_secs=_retry_str)
            if self._msg_manager
            else f"Lyra is currently unavailable. Please try again in {retry_secs}s."
        )
        from ..messaging.message import Response

        try:
            await self.dispatch_response(msg, Response(content=_unavail))
        except Exception as exc:
            log.exception("dispatch_response failed for fast-fail reply: %s", exc)
        return True

    def record_circuit_success(self) -> None:
        if self.circuit_registry is not None:
            for name in ("claude-cli", "hub"):
                cb = self.circuit_registry.get(name)
                if cb is not None:
                    cb.record_success()

    def record_circuit_failure(self, exc: BaseException) -> None:
        if self.circuit_registry is not None:
            _hub_cb = self.circuit_registry.get("hub")
            if _hub_cb is not None:
                _hub_cb.record_failure()
            if isinstance(exc, ProviderError):
                _cli_cb = self.circuit_registry.get("claude-cli")
                if _cli_cb is not None:
                    _cli_cb.record_failure()
