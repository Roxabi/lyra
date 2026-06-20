"""Platform-meta helpers for adapter typing scope resolution (#1931)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from factory.core.messaging.message import DiscordMeta, InboundMessage, TelegramMeta

if TYPE_CHECKING:
    pass


def cancel_typing_for_inbound(adapter: Any, inbound: InboundMessage) -> None:
    """Cancel typing indicator using platform_meta routing fields."""
    pm = inbound.platform_meta
    if isinstance(pm, DiscordMeta):
        adapter._cancel_typing(pm.thread_id or pm.channel_id)
    elif isinstance(pm, TelegramMeta):
        adapter._cancel_typing(pm.chat_id)
