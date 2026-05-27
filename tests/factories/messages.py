"""Message factory helpers for tests."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from lyra.core.auth.trust import TrustLevel
from lyra.core.messaging.message import (
    DiscordMeta,
    InboundMessage,
    Platform,
    TelegramMeta,
)

if TYPE_CHECKING:
    from lyra.core.messaging.message import Attachment, RoutingContext

__all__ = [
    "make_debouncer_msg",
    "make_dispatcher_msg",
    "make_inbound_message",
    "make_message",
    "make_pairing_message",
    "make_routing_inbound",
]


def make_message(
    content: str = "hello",
    platform: str = "telegram",
    bot_id: str = "main",
    user_id: str = "alice",
    *,
    is_admin: bool = False,
) -> InboundMessage:
    """Build a minimal InboundMessage for command router tests.

    Auto-parses CommandContext and attaches it to the message, mirroring
    what the Hub pipeline does.
    """
    from lyra.core.commands.command_parser import CommandParser

    _parser = CommandParser()
    cmd_ctx = _parser.parse(content)
    return InboundMessage(
        id="msg-test-1",
        platform=platform,
        bot_id=bot_id,
        scope_id="chat:42",
        user_id=user_id,
        user_name="Alice",
        is_mention=False,
        text=content,
        text_raw=content,
        timestamp=datetime.now(timezone.utc),
        platform_meta=TelegramMeta(chat_id=42),
        trust_level=TrustLevel.TRUSTED,
        is_admin=is_admin,
        command=cmd_ctx,
    )


def make_inbound_message(  # noqa: PLR0913
    platform: str = "telegram",
    bot_id: str = "main",
    user_id: str = "alice",
    scope_id: str | None = None,
    platform_meta=None,
    modality: str | None = None,
) -> InboundMessage:
    """Build a minimal InboundMessage for hub tests."""
    from lyra.core.messaging.message import GenericMeta

    if platform == "telegram":
        _scope = scope_id if scope_id is not None else "chat:42"
        _meta = platform_meta if platform_meta is not None else TelegramMeta(chat_id=42)
    elif platform == "discord":
        _scope = scope_id if scope_id is not None else "channel:333"
        _meta = (
            platform_meta
            if platform_meta is not None
            else DiscordMeta(
                channel_id=333, message_id=555, guild_id=111, channel_type="text"
            )
        )
    else:
        _scope = scope_id if scope_id is not None else f"{platform}:default"
        _meta = platform_meta if platform_meta is not None else GenericMeta()
    kwargs: dict = dict(
        id="msg-1",
        platform=platform,
        bot_id=bot_id,
        scope_id=_scope,
        user_id=user_id,
        user_name="Alice",
        is_mention=False,
        text="hello",
        text_raw="hello",
        timestamp=datetime.now(timezone.utc),
        platform_meta=_meta,
        trust_level=TrustLevel.TRUSTED,
    )
    if modality is not None:
        kwargs["modality"] = modality
    if modality == "voice":
        kwargs["text"] = ""
        kwargs["text_raw"] = ""
    return InboundMessage(**kwargs)


def make_dispatcher_msg() -> InboundMessage:
    """Build a minimal InboundMessage for OutboundDispatcher tests."""
    return InboundMessage(
        id="msg-1",
        platform="telegram",
        bot_id="main",
        scope_id="chat:123",
        user_id="tg:user:42",
        user_name="Alice",
        is_mention=False,
        text="hello",
        text_raw="hello",
        timestamp=datetime.now(timezone.utc),
        platform_meta=TelegramMeta(chat_id=123),
        trust_level=TrustLevel.TRUSTED,
    )


def make_debouncer_msg(
    text: str = "hello",
    msg_id: str = "msg-1",
    is_mention: bool = False,
    attachments: list[Attachment] | None = None,
) -> InboundMessage:
    """Build a minimal InboundMessage for debouncer tests.

    Supports msg_id, is_mention, attachments.
    """
    return InboundMessage(
        id=msg_id,
        platform="telegram",
        bot_id="main",
        scope_id="chat:1",
        user_id="tg:user:1",
        user_name="Alice",
        is_mention=is_mention,
        text=text,
        text_raw=text,
        timestamp=datetime.now(timezone.utc),
        platform_meta=TelegramMeta(chat_id=1),
        trust_level=TrustLevel.TRUSTED,
        attachments=attachments or [],
    )


_PAIRING_ADMIN_ID = "admin-user-1"
_PAIRING_USER_ID = "regular-user-1"


def make_pairing_message(  # noqa: PLR0913
    content: str = "hello",
    platform: Platform = Platform.TELEGRAM,
    bot_id: str = "main",
    user_id: str = _PAIRING_USER_ID,
    is_group: bool = False,
    guild_id: int | None = None,
    *,
    is_admin: bool = False,
) -> InboundMessage:
    """Build a minimal InboundMessage for pairing tests."""
    if platform == Platform.DISCORD:
        scope = "channel:1"
        meta = DiscordMeta(
            channel_id=1, message_id=1, guild_id=guild_id, channel_type="text"
        )
    else:
        scope = "chat:42"
        meta = TelegramMeta(chat_id=42, is_group=is_group)

    return InboundMessage(
        id="msg-test-1",
        platform=platform.value,
        bot_id=bot_id,
        scope_id=scope,
        user_id=user_id,
        user_name="Tester",
        is_mention=False,
        text=content,
        text_raw=content,
        timestamp=datetime.now(timezone.utc),
        platform_meta=meta,
        trust_level=TrustLevel.TRUSTED,
        is_admin=is_admin,
    )


def make_routing_inbound(routing: RoutingContext | None = None) -> InboundMessage:
    """Build a minimal InboundMessage for routing context tests."""
    return InboundMessage(
        id="msg-1",
        platform="telegram",
        bot_id="main",
        scope_id="chat:123",
        user_id="tg:user:42",
        user_name="Alice",
        is_mention=False,
        text="hello",
        text_raw="hello",
        timestamp=datetime.now(timezone.utc),
        trust_level=TrustLevel.TRUSTED,
        platform_meta=TelegramMeta(chat_id=123),
        routing=routing,
    )
