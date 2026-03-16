"""Inbound message normalization for DiscordAdapter."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

import discord

from lyra.adapters.discord_formatting import extract_attachments
from lyra.core.message import (
    InboundMessage,
    Platform,
    RoutingContext,
)
from lyra.core.trust import TrustLevel

if TYPE_CHECKING:
    from lyra.adapters.discord import DiscordAdapter

log = logging.getLogger("lyra.adapters.discord")


def normalize(  # noqa: PLR0913 — all kwargs are platform-specific routing context
    adapter: "DiscordAdapter",
    raw: Any,
    *,
    thread_id: int | None = None,
    channel_id: int | None = None,
    trust_level: TrustLevel = TrustLevel.TRUSTED,
    is_admin: bool = False,  # REQUIRED: always pass is_admin=identity.is_admin — do not rely on default  # noqa: E501
) -> InboundMessage:
    """Convert a discord.py Message (or SimpleNamespace) to InboundMessage."""
    is_mention = adapter._bot_user is not None and adapter._bot_user in raw.mentions

    # Strip @mention prefix so content reaches the agent clean
    text = raw.content
    if is_mention:
        if adapter._mention_re is None and adapter._bot_user is not None:
            adapter._mention_re = re.compile(rf"<@!?{adapter._bot_user.id}>")
    if is_mention and adapter._mention_re:
        text = adapter._mention_re.sub("", text).strip()

    # Resolve channel routing (pre-resolved by on_message after thread)
    resolved_channel_id: int = channel_id if channel_id is not None else raw.channel.id
    resolved_thread_id: int | None = thread_id

    is_thread = isinstance(raw.channel, discord.Thread)

    # If no override, check if already in a thread
    if resolved_thread_id is None and is_thread:
        resolved_thread_id = raw.channel.id

    scope_id = (
        f"thread:{resolved_thread_id}"
        if resolved_thread_id
        else f"channel:{resolved_channel_id}"
    )

    # Detect channel type
    channel_type: str = "text"
    if is_thread:
        channel_type = "thread"
    elif isinstance(raw.channel, discord.ForumChannel):
        channel_type = "forum"
    elif isinstance(raw.channel, discord.VoiceChannel):
        channel_type = "voice"

    timestamp = raw.created_at
    user_id = f"dc:user:{raw.author.id}"

    log.debug(
        "Normalizing discord message id=%s from user_id=%s",
        raw.id,
        user_id,
    )

    _display_name = getattr(raw.author, "display_name", None)
    attachments = extract_attachments(getattr(raw, "attachments", None) or [])
    _reference = getattr(raw, "reference", None)
    reply_to_id: str | None = (
        str(_reference.message_id)
        if _reference is not None and _reference.message_id is not None
        else None
    )
    platform_meta = {
        "guild_id": raw.guild.id if raw.guild else None,
        "channel_id": resolved_channel_id,
        # INVARIANT: always original message id, never thread.id
        "message_id": raw.id,
        "thread_id": resolved_thread_id,
        "channel_type": channel_type,
    }
    routing = RoutingContext(
        platform=Platform.DISCORD.value,
        bot_id=adapter._bot_id,
        scope_id=scope_id,
        thread_id=(str(resolved_thread_id) if resolved_thread_id is not None else None),
        reply_to_message_id=str(raw.id),
        platform_meta=dict(platform_meta),
    )
    return InboundMessage(
        id=(f"discord:{user_id}:{int(timestamp.timestamp())}:{raw.id}"),
        platform=Platform.DISCORD.value,
        bot_id=adapter._bot_id,
        scope_id=scope_id,
        user_id=user_id,
        user_name=(_display_name if _display_name is not None else raw.author.name),
        is_mention=is_mention,
        text=text,
        text_raw=raw.content,
        attachments=attachments,
        timestamp=timestamp,
        trust="user",
        trust_level=trust_level,
        is_admin=is_admin,
        platform_meta=platform_meta,
        routing=routing,
        reply_to_id=reply_to_id,
    )
