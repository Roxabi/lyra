"""Voice session management for DiscordAdapter (issue #255)."""

from __future__ import annotations

import ctypes.util
import enum
import logging
import queue
import shutil
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

_FRAME_SIZE = 3840  # 20 ms × 48 kHz stereo 16-bit PCM


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class VoiceDependencyError(Exception):
    """Raised when a required voice system dependency (libopus, ffmpeg) is missing."""


class VoiceAlreadyActiveError(Exception):
    """Raised when join() is called for a guild that already has an active session."""

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
    try:
        import discord.voice_client  # noqa: F401
    except ImportError as exc:
        raise VoiceDependencyError(
            "discord.py[voice] not installed — run: pip install discord.py[voice]"
        ) from exc
    if not discord.opus.is_loaded():
        opus_lib = ctypes.util.find_library("opus")
        if opus_lib is None:
            raise VoiceDependencyError(
                "libopus not found — install libopus-dev (Ubuntu) or libopus (macOS)"
            )
        discord.opus.load_opus(opus_lib)
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

    maxsize=0: put_nowait() never raises queue.Full.
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
        """Return True if the VoiceClient is still connected."""
        return self.voice_client.is_connected()


# ---------------------------------------------------------------------------
# Session manager
# ---------------------------------------------------------------------------


class VoiceSessionManager:
    """Manages per-guild Discord voice sessions for DiscordAdapter."""

    def __init__(self, client: discord.Client) -> None:
        self._client = client
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

    def invalidate(self, guild_id: str) -> None:
        """Remove the session without disconnecting (VoiceClient already stale)."""
        self._sessions.pop(guild_id, None)
        log.info("Voice session invalidated: guild=%s", guild_id)

    def get(self, guild_id: str) -> VoiceSession | None:
        """Return the active VoiceSession for guild_id, or None."""
        return self._sessions.get(guild_id)
