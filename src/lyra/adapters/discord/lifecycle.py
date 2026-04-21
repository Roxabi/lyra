"""Discord adapter lifecycle callbacks (on_ready, on_guild_join)."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from lyra.adapters.discord.adapter import DiscordAdapter

log = logging.getLogger(__name__)


async def on_ready(adapter: "DiscordAdapter") -> None:
    """Cache bot user and compile mention regex on login.

    Called by DiscordAdapter.on_ready() after super().on_ready() if needed.
    """
    adapter._bot_user = adapter.user
    if adapter.user is not None:
        adapter._mention_re = re.compile(rf"<@!?{adapter.user.id}>")
    log.info(
        "Discord bot ready: %s (id=%s)",
        adapter.user,
        getattr(adapter.user, "id", "?"),
    )
    if not adapter.intents.message_content:
        log.warning(
            "message_content intent is disabled — "
            "guild message content will be empty. "
            "Enable 'Message Content Intent' in the Developer Portal."
        )
    # Restore hot threads from ThreadStore on startup.
    if adapter._thread_store is not None:
        try:
            from lyra.adapters.discord.discord_threads import restore_hot_threads

            adapter._owned_threads = await restore_hot_threads(
                adapter._thread_store, adapter._bot_id, adapter._thread_hot_hours
            )
        except Exception:
            log.exception("ThreadStore: failed to restore owned threads")
    # Sync app_commands tree for each guild (guild-scoped = instant).
    for guild in adapter.guilds:
        try:
            await adapter.tree.sync(guild=guild)
            log.info("Synced app_commands for guild %s", guild.id)
        except Exception:
            log.warning(
                "Failed to sync app_commands for guild %s",
                guild.id,
                exc_info=True,
            )


async def on_guild_join(adapter: "DiscordAdapter", guild: discord.Guild) -> None:
    """Sync app_commands tree when the bot joins a new guild.

    Called by DiscordAdapter.on_guild_join().
    """
    try:
        await adapter.tree.sync(guild=guild)
        log.info("Synced app_commands for new guild %s", guild.id)
    except Exception:
        log.warning(
            "Failed to sync app_commands for new guild %s",
            guild.id,
            exc_info=True,
        )


async def on_voice_state_update(
    adapter: "DiscordAdapter",
    member: discord.Member,
    before: discord.VoiceState,
    after: discord.VoiceState,
) -> None:
    """Invalidate stale voice session when the bot is forcibly disconnected.

    Called by DiscordAdapter.on_voice_state_update().
    """
    bot_user = adapter._bot_user
    if bot_user is None or member.id != bot_user.id or after.channel is not None:
        return
    # member.guild is always set for voice state events (guild-only, no DM voice).
    guild_id = str(member.guild.id)
    adapter._vsm.invalidate(guild_id)
