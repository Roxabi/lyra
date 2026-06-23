"""WebWireParser — translates the web smoke payload → InboundMessage."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from factory.core.auth.trust import TrustLevel

if TYPE_CHECKING:
    from factory.core.messaging.message import InboundMessage
    from factory.inbound.context import InboundContext


class _WebNormalizer(Protocol):
    """Narrow protocol: the normalize signature used by WebWireParser.

    Satisfied by ``WebAdapter`` without importing it (stage-axis invariant:
    ``factory.inbound`` must not import ``factory.adapters``, ADR-073 / #1287).
    """

    def normalize(
        self,
        raw: Any,
        *,
        trust_level: TrustLevel = TrustLevel.TRUSTED,
        is_admin: bool = False,
    ) -> "InboundMessage": ...


class WebWireParser:
    """WireParser implementation for the web smoke adapter.

    Delegates normalization to the adapter's ``normalize`` method (mirrors
    ``TelegramWireParser``).  The web payload is a plain dict
    ``{"agent", "text", "session_id"}``.  Validation errors (unknown agent /
    empty text) are raised by ``normalize`` as ``ValueError`` and surfaced
    synchronously by the HTTP layer *before* the pipeline runs, so the in-
    pipeline parse never raises on the happy path.
    """

    def __init__(self, adapter: _WebNormalizer) -> None:
        self._adapter = adapter

    def parse(self, raw: Any, ctx: "InboundContext") -> "InboundMessage | None":
        """Parse the web request dict into an ``InboundMessage``.

        ``ctx`` is unused at parse time but kept on the protocol signature for
        interface consistency.  Web messages are smoke-local and trusted.
        """
        del ctx  # unused; kept for WireParser protocol compatibility
        return self._adapter.normalize(raw, trust_level=TrustLevel.TRUSTED)
