"""TelegramWireParser — translates aiogram Message → InboundMessage."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from factory.core.auth.trust import TrustLevel

if TYPE_CHECKING:
    from factory.adapters.telegram.telegram import TelegramAdapter
    from factory.core.messaging.message import InboundMessage
    from factory.inbound.context import InboundContext


class TelegramWireParser:
    """WireParser implementation for Telegram (aiogram).

    Delegates normalization to ``TelegramAdapter.normalize`` to preserve all
    existing behaviour.  Bot-author messages are filtered early (return ``None``)
    before reaching the router.
    """

    def __init__(self, adapter: "TelegramAdapter") -> None:
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
