"""Voice command handlers for DiscordAdapter (!join, !leave)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from lyra.adapters.discord_voice import (
    VoiceAlreadyActiveError,
    VoiceDependencyError,
    VoiceMode,
)
from lyra.core.command_parser import CommandParser
from lyra.core.trust import TrustLevel

if TYPE_CHECKING:
    from lyra.adapters.discord import DiscordAdapter

log = logging.getLogger(__name__)

_command_parser = CommandParser()


async def reply_safe(message: Any, text: str, *, label: str) -> None:
    """Send a reply, logging a warning on failure."""
    try:
        await message.reply(text)
    except Exception as exc:
        log.warning(
            "Failed to send %s reply for message_id=%s: %s",
            label,
            message.id,
            exc,
        )


async def handle_leave_command(
    adapter: "DiscordAdapter", message: Any, guild_id: str
) -> None:
    """Execute !leave: disconnect if active, reply with outcome."""
    log.info(
        "voice_cmd cmd=leave user=%s guild=%s",
        getattr(message.author, "id", "?"),
        guild_id,
    )
    if adapter._vsm.get(guild_id) is None:
        await reply_safe(message, "I'm not in a voice channel.", label="not-in-channel")
    else:
        await adapter._vsm.leave(guild_id)
        await reply_safe(message, "Left the voice channel.", label="leave")


async def handle_join_command(
    adapter: "DiscordAdapter",
    message: Any,
    guild: Any,
    args: str,
    trust: TrustLevel = TrustLevel.TRUSTED,
) -> None:
    """Execute !join / !join stay: connect to user's voice channel."""
    voice_state = getattr(message.author, "voice", None)
    if voice_state is None or voice_state.channel is None:
        await reply_safe(message, "Join a voice channel first.", label="not-in-voice")
        return
    mode = (
        VoiceMode.PERSISTENT
        if args.strip().lower().split()[:1] == ["stay"]
        else VoiceMode.TRANSIENT
    )
    if mode == VoiceMode.PERSISTENT and trust < TrustLevel.TRUSTED:
        await reply_safe(
            message,
            "Persistent mode requires elevated permissions.",
            label="persistent-denied",
        )
        mode = VoiceMode.TRANSIENT
    try:
        await adapter._vsm.join(guild, voice_state.channel, mode)
    except VoiceAlreadyActiveError:
        await reply_safe(message, "Already in a voice channel.", label="already-active")
    except VoiceDependencyError as exc:
        log.error("Voice dependency error on join: %s", exc)
        await reply_safe(
            message, "Voice is not available right now.", label="voice-unavailable"
        )


async def handle_voice_command(
    adapter: "DiscordAdapter",
    message: Any,
    trust: TrustLevel = TrustLevel.TRUSTED,
) -> bool:
    """Detect and handle !join / !join stay / !leave voice commands.

    Returns True if a voice command was handled (caller should return early).
    Returns False if the message is not a voice command.
    Both ! and / prefixes are accepted (CommandParser handles both).
    Voice commands are guild-only; callers must not invoke for DMs.
    """
    cmd = _command_parser.parse(message.content.strip())
    if cmd is None or cmd.name not in ("join", "leave"):
        return False
    guild = message.guild
    guild_id = str(guild.id)
    if cmd.name == "leave":
        if trust < TrustLevel.TRUSTED:
            await reply_safe(
                message,
                "You don't have permission to use this command.",
                label="leave-denied",
            )
            return True
        await handle_leave_command(adapter, message, guild_id)
    else:
        await handle_join_command(adapter, message, guild, cmd.args, trust=trust)
    return True
