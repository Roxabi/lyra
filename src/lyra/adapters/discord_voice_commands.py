"""Voice command handlers for DiscordAdapter (!join, !leave, /join, /leave)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from lyra.adapters.discord_voice import (
    VoiceAlreadyActiveError,
    VoiceDependencyError,
    VoiceMode,
)
from lyra.core.auth.trust import TrustLevel
from lyra.core.commands.command_parser import CommandParser
from lyra.core.commands.command_registry import CommandParam, PlatformCommand

if TYPE_CHECKING:
    import discord

    from lyra.adapters.discord import DiscordAdapter

log = logging.getLogger(__name__)

_command_parser = CommandParser()

# Voice command metadata — consumed by command_registry.collect_commands()
# and by register_voice_app_commands() for Discord slash command registration.
VOICE_COMMANDS: list[PlatformCommand] = [
    PlatformCommand(
        name="/join",
        description="Join your voice channel",
        params=[
            CommandParam(
                name="mode",
                description="Connection mode: transient or stay",
                required=False,
                choices=["transient", "stay"],
            ),
        ],
    ),
    PlatformCommand(
        name="/leave",
        description="Leave the voice channel",
    ),
]


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
    _elevated = {TrustLevel.TRUSTED, TrustLevel.OWNER}
    if mode == VoiceMode.PERSISTENT and trust not in _elevated:
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
        if trust not in {TrustLevel.TRUSTED, TrustLevel.OWNER}:
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


def _resolve_slash_trust(adapter: "DiscordAdapter", user_id: str) -> TrustLevel:
    """Resolve trust level for a slash command interaction user.

    Slash commands are out-of-band Discord interactions that don't flow through
    the inbound message bus. Trust is delegated to the Hub authenticator (C3).
    """
    if adapter._resolve_identity_fn is None:
        log.warning(
            "_resolve_slash_trust: no identity resolver wired (NATS mode?)"
            " — falling back to TrustLevel.PUBLIC for user_id=%s",
            user_id,
        )
        return TrustLevel.PUBLIC
    identity = adapter._resolve_identity_fn(user_id, "discord", adapter._bot_id)
    if identity.trust_level == TrustLevel.BLOCKED:
        return TrustLevel.BLOCKED
    return identity.trust_level


async def _handle_join_slash(
    interaction: Any,
    adapter: "DiscordAdapter",
    mode: str,
) -> None:
    """Handle the /join slash command interaction."""
    import discord as _discord

    if interaction.guild is None:
        await interaction.response.send_message(
            "This command can only be used in a server.", ephemeral=True
        )
        return
    # Auth check — mirror the text-command auth flow
    trust = _resolve_slash_trust(adapter, str(interaction.user.id))
    if trust == TrustLevel.BLOCKED:
        await interaction.response.send_message(
            "You don't have permission to use this command.",
            ephemeral=True,
        )
        return
    guild = interaction.guild
    # interaction.user is already a Member in guild context
    member = interaction.user
    voice_state = member.voice
    if voice_state is None or voice_state.channel is None:
        await interaction.response.send_message(
            "Join a voice channel first.", ephemeral=True
        )
        return
    channel = voice_state.channel
    if not isinstance(channel, _discord.VoiceChannel):
        await interaction.response.send_message(
            "Stage channels are not supported.", ephemeral=True
        )
        return
    voice_mode = VoiceMode.PERSISTENT if mode == "stay" else VoiceMode.TRANSIENT
    _elevated = {TrustLevel.TRUSTED, TrustLevel.OWNER}
    if voice_mode == VoiceMode.PERSISTENT and trust not in _elevated:
        voice_mode = VoiceMode.TRANSIENT
    try:
        await adapter._vsm.join(guild, channel, voice_mode)
        label = "persistent" if voice_mode == VoiceMode.PERSISTENT else "transient"
        await interaction.response.send_message(
            f"Joined {channel.name} ({label}).", ephemeral=True
        )
    except VoiceAlreadyActiveError:
        await interaction.response.send_message(
            "Already in a voice channel.", ephemeral=True
        )
    except VoiceDependencyError as exc:
        log.error("Voice dependency error on /join slash: %s", exc)
        await interaction.response.send_message(
            "Voice is not available right now.", ephemeral=True
        )


def register_voice_app_commands(
    tree: "discord.app_commands.CommandTree[Any]",
    adapter: "DiscordAdapter",
) -> None:
    """Register /join and /leave as native Discord slash commands."""
    import discord as _discord

    @tree.command(name="join", description="Join your voice channel")
    @_discord.app_commands.describe(
        mode="Connection mode: transient or stay (persistent)"
    )
    @_discord.app_commands.choices(
        mode=[
            _discord.app_commands.Choice(name="transient", value="transient"),
            _discord.app_commands.Choice(name="stay", value="stay"),
        ]
    )
    async def join_slash(
        interaction: _discord.Interaction, mode: str = "transient"
    ) -> None:
        await _handle_join_slash(interaction, adapter, mode)

    @tree.command(name="leave", description="Leave the voice channel")
    async def leave_slash(interaction: _discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return
        trust = _resolve_slash_trust(adapter, str(interaction.user.id))
        if trust not in {TrustLevel.TRUSTED, TrustLevel.OWNER}:
            await interaction.response.send_message(
                "You don't have permission to use this command.",
                ephemeral=True,
            )
            return
        guild_id = str(interaction.guild.id)
        if adapter._vsm.get(guild_id) is None:
            await interaction.response.send_message(
                "I'm not in a voice channel.", ephemeral=True
            )
        else:
            await adapter._vsm.leave(guild_id)
            await interaction.response.send_message(
                "Left the voice channel.", ephemeral=True
            )
