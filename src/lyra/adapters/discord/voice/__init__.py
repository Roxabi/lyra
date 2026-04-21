"""Discord voice sub-package (VoiceSession, VoiceSessionManager, voice commands)."""

from __future__ import annotations

from lyra.adapters.discord.voice.discord_voice import (
    VoiceAlreadyActiveError,
    VoiceDependencyError,
    VoiceMode,
    VoiceSession,
    VoiceSessionManager,
)
from lyra.adapters.discord.voice.discord_voice_commands import (
    VOICE_COMMANDS,
    handle_voice_command,
    register_voice_app_commands,
)

__all__ = [
    "VoiceAlreadyActiveError",
    "VoiceDependencyError",
    "VoiceMode",
    "VoiceSession",
    "VoiceSessionManager",
    "VOICE_COMMANDS",
    "handle_voice_command",
    "register_voice_app_commands",
]
