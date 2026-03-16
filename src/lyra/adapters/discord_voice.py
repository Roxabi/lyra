"""Voice session management for DiscordAdapter (issue #255)."""

from __future__ import annotations

import ctypes.util
import enum
import logging
import queue
import shutil
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import discord

from lyra.core.command_parser import CommandParser
from lyra.core.message import OutboundAudioChunk
from lyra.core.trust import TrustLevel

if TYPE_CHECKING:
    from lyra.adapters.discord import DiscordAdapter

log = logging.getLogger(__name__)

_command_parser = CommandParser()

_FRAME_SIZE = 3840  # 20 ms × 48 kHz stereo 16-bit PCM


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class VoiceDependencyError(Exception):
    """Raised when a required voice system dependency (libopus, ffmpeg) is missing."""


class VoiceAlreadyActiveError(Exception):
    """Raised when join() is called for a guild that already has an active session.

    Attributes:
        guild_id: Public API — Slice C (voice commands) reads this to surface
            "Already in a voice channel." to the user.
    """

    def __init__(self, guild_id: str) -> None:
        super().__init__(f"Voice session already active for guild {guild_id!r}")
        self.guild_id = guild_id


# ---------------------------------------------------------------------------
# Dependency check (lazy, runs once per process)
# ---------------------------------------------------------------------------

_deps_checked = False


def _check_voice_deps() -> None:
    """Verify libopus, ffmpeg, and discord.py[voice] are present.

    Lazy: runs once per process, guarded by _deps_checked flag.
    """
    global _deps_checked
    if _deps_checked:
        return
    if not discord.opus.is_loaded():
        opus_lib = ctypes.util.find_library("opus")
        if opus_lib is None:
            raise VoiceDependencyError(
                "libopus not found — install libopus-dev (Ubuntu) or libopus (macOS)"
            )
        try:
            discord.opus.load_opus(opus_lib)
        except OSError as exc:
            raise VoiceDependencyError(
                f"libopus failed to load ({opus_lib}) — {exc}"
            ) from exc
    if shutil.which("ffmpeg") is None:
        raise VoiceDependencyError(
            "ffmpeg not found — install ffmpeg (apt install ffmpeg)"
        )
    _deps_checked = True


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class VoiceMode(enum.Enum):
    TRANSIENT = "transient"
    PERSISTENT = "persistent"


class PCMQueueSource(discord.AudioSource):
    """Thread-safe PCM audio source backed by queue.Queue.

    read() is called from a non-async threading.Thread by the VoiceClient.
    push() / push_eof() are called from the asyncio event loop.
    queue.Queue provides the required thread-safety without additional locking.

    maxsize=0: put_nowait() never raises queue.Full. The queue is intentionally
    unbounded — push rate is bounded by TTS output, not by a tight loop, so
    memory growth in normal operation is negligible.
    read() returns exactly 3840 bytes (20 ms frame) or b"" to signal EOF.
    On queue.Empty (timeout), returns 3840 null bytes (silence) to keep
    VoiceClient alive while waiting for audio.

    """

    def __init__(self) -> None:
        self._q: queue.Queue[bytes | None] = queue.Queue(maxsize=0)
        self._buf: bytes = b""

    def push(self, chunk: bytes) -> None:
        """Buffer chunk and enqueue complete 3840-byte frames."""
        self._buf += chunk
        while len(self._buf) >= _FRAME_SIZE:
            self._q.put_nowait(self._buf[:_FRAME_SIZE])
            self._buf = self._buf[_FRAME_SIZE:]

    def push_eof(self) -> None:
        """Flush remaining buffer (padded) and enqueue None sentinel.

        Safe to call multiple times — each call enqueues one sentinel.
        The VoiceClient stops on the first b"" return and never calls read() again,
        so extra sentinels are harmless.
        """
        if self._buf:
            padded = self._buf.ljust(_FRAME_SIZE, b"\x00")
            self._q.put_nowait(padded)
            self._buf = b""
        self._q.put_nowait(None)

    def read(self) -> bytes:
        """Return the next 3840-byte frame, b"\\x00"*3840 on underrun, or b"" on EOF."""
        try:
            # 5× frame interval (20 ms); drains fast on leave()
            item = self._q.get(timeout=0.1)
        except queue.Empty:
            return b"\x00" * _FRAME_SIZE  # silence frame — keeps VoiceClient alive
        if item is None:
            return b""
        return item

    def is_opus(self) -> bool:
        return False


@dataclass
class VoiceSession:
    guild_id: str
    voice_client: discord.VoiceClient
    channel_id: int
    mode: VoiceMode
    source: PCMQueueSource = field(default_factory=PCMQueueSource)

    def is_active(self) -> bool:
        """Return True if the VoiceClient is still connected.

        Note: may transiently return True during a network partition before
        discord.py detects the disconnect. Callers (e.g. Slice B) should not
        treat True as a hard liveness guarantee.
        """
        return self.voice_client.is_connected()


# ---------------------------------------------------------------------------
# Session manager
# ---------------------------------------------------------------------------


class VoiceSessionManager:
    """Manages per-guild Discord voice sessions for DiscordAdapter."""

    def __init__(self) -> None:
        self._sessions: dict[str, VoiceSession] = {}

    async def join(
        self,
        guild: discord.Guild,
        channel: discord.VoiceChannel,
        mode: VoiceMode,
    ) -> VoiceSession:
        """Connect to a voice channel and store the session.

        Raises VoiceAlreadyActiveError if a session already exists for this guild.
        Raises VoiceDependencyError if libopus or ffmpeg are missing.
        VoiceClient.play() is NOT called here — deferred to render_voice_stream()
        (Slice B). Calling play() at join time would start the read loop immediately,
        draining silence frames before any audio is ready.
        """
        guild_id = str(guild.id)
        if guild_id in self._sessions:
            raise VoiceAlreadyActiveError(guild_id)
        _check_voice_deps()
        source = PCMQueueSource()
        voice_client = await channel.connect()
        session = VoiceSession(
            guild_id=guild_id,
            voice_client=voice_client,
            channel_id=channel.id,
            mode=mode,
            source=source,
        )
        self._sessions[guild_id] = session
        log.info(
            "Voice session joined: guild=%s channel=%s mode=%s",
            guild_id,
            channel.id,
            mode,
        )
        return session

    async def leave(self, guild_id: str) -> None:
        """Disconnect and remove the session for guild_id.

        No-op if no session exists.
        """
        session = self._sessions.get(guild_id)
        if session is None:
            return
        session.source.push_eof()
        await session.voice_client.disconnect()
        del self._sessions[guild_id]
        log.info("Voice session left: guild=%s", guild_id)

    async def stream(
        self,
        guild_id: str,
        chunks: AsyncIterator[OutboundAudioChunk],
    ) -> None:
        """Drain TTS chunks into the active voice session's PCMQueueSource.

        Starts VoiceClient.play() if not already playing.
        push_eof() is called unconditionally after the loop as a safety net
        for non-conforming producers. Extra sentinels are harmless (idempotent).
        Auto-leaves if mode is TRANSIENT.
        """
        session = self._sessions.get(guild_id)
        if session is None:
            log.warning("No active voice session for guild %s", guild_id)
            return
        if session.voice_client.is_playing():
            log.warning(
                "Already streaming to guild %s — concurrent stream dropped", guild_id
            )
            return
        # Fresh source per stream: avoids stale None sentinels from prior streams.
        session.source = PCMQueueSource()
        session.voice_client.play(session.source)
        try:
            async for chunk in chunks:
                session.source.push(chunk.chunk_bytes)
                if chunk.is_final:
                    session.source.push_eof()
        finally:
            session.source.push_eof()  # safety net: idempotent, extra sentinel harmless
        if session.mode == VoiceMode.TRANSIENT:
            await self.leave(guild_id)

    def invalidate(self, guild_id: str) -> None:
        """Remove the session without disconnecting (VoiceClient already stale)."""
        self._sessions.pop(guild_id, None)
        log.info("Voice session invalidated: guild=%s", guild_id)

    def get(self, guild_id: str) -> VoiceSession | None:
        """Return the active VoiceSession for guild_id, or None."""
        return self._sessions.get(guild_id)

    async def leave_all(self) -> None:
        """Disconnect and remove all active voice sessions.

        Called during adapter shutdown.
        """
        for guild_id in list(self._sessions):
            await self.leave(guild_id)


# ---------------------------------------------------------------------------
# Voice command handlers (extracted from DiscordAdapter)
# ---------------------------------------------------------------------------


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
