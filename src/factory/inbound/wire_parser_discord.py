"""DiscordWireParser — translates discord.py Message → InboundMessage.

NOTE: Audio short-circuit and voice command short-circuit REMAIN in
``discord_inbound.handle_message`` BEFORE the pipeline call.  This parser
only handles text messages that reach the pipeline.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from factory.core.auth.trust import TrustLevel

if TYPE_CHECKING:
    from factory.core.messaging.message import InboundMessage
    from factory.inbound.context import InboundContext


class _DiscordNormalizer(Protocol):
    """Narrow protocol: the normalize signature used by DiscordWireParser.

    Satisfied by ``DiscordAdapter`` without importing it.
    """

    def normalize(
        self,
        raw: Any,
        *,
        thread_id: int | None = None,
        channel_id: int | None = None,
        trust_level: TrustLevel = TrustLevel.TRUSTED,
        is_admin: bool = False,
    ) -> "InboundMessage": ...


class DiscordWireParser:
    """WireParser implementation for Discord (discord.py).

    Delegates normalization to the adapter's ``normalize`` method to preserve all
    existing behaviour.  Bot-author messages are filtered early (return ``None``).

    ``thread_id`` and ``channel_id`` are passed as ``None`` here — the resolved
    thread ID is set later by ``_discord_pre_session_hook`` (auto-thread creation)
    via ``dataclasses.replace`` on the returned ``InboundMessage``.
    """

    def __init__(self, adapter: _DiscordNormalizer) -> None:
        self._adapter = adapter

    def parse(self, raw: Any, ctx: "InboundContext") -> "InboundMessage | None":
        """Parse a discord.py ``Message`` into an ``InboundMessage``.

        Returns ``None`` when the message was sent by a bot (own-message guard).
        ``ctx`` is unused at parse time but kept on the protocol signature for
        interface consistency.
        """
        del ctx  # unused; kept for WireParser protocol compatibility
        if raw.author.bot:
            return None
        return self._adapter.normalize(
            raw,
            thread_id=None,
            channel_id=None,
            trust_level=TrustLevel.PUBLIC,
            is_admin=False,
        )
