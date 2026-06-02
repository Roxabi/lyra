"""TelegramWireParser — translates aiogram Message → InboundMessage."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from factory.core.auth.trust import TrustLevel

if TYPE_CHECKING:
    from factory.core.messaging.message import InboundMessage
    from factory.inbound.context import InboundContext


class _TelegramNormalizer(Protocol):
    """Narrow protocol: the normalize signature used by TelegramWireParser.

    Satisfied by ``TelegramAdapter`` without importing it.
    """

    def normalize(
        self,
        raw: Any,
        *,
        trust_level: TrustLevel = TrustLevel.TRUSTED,
        is_admin: bool = False,
    ) -> "InboundMessage": ...


class TelegramWireParser:
    """WireParser implementation for Telegram (aiogram).

    Delegates normalization to the adapter's ``normalize`` method to preserve all
    existing behaviour.  Bot-author messages are filtered early (return ``None``)
    before reaching the router.
    """

    def __init__(self, adapter: _TelegramNormalizer) -> None:
        self._adapter = adapter

    def parse(self, raw: Any, ctx: "InboundContext") -> "InboundMessage | None":
        """Parse an aiogram ``Message`` into an ``InboundMessage``.

        Returns ``None`` when the message has no sender or was sent by a bot
        (own-message guard).  ``ctx`` is unused at parse time but kept on the
        protocol signature for interface consistency.
        """
        del ctx  # unused; kept for WireParser protocol compatibility
        if not raw.from_user or raw.from_user.is_bot:
            return None
        return self._adapter.normalize(
            raw,
            trust_level=TrustLevel.PUBLIC,
            is_admin=False,
        )
