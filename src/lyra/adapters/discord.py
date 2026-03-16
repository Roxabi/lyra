from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

import discord

if TYPE_CHECKING:
    from lyra.core.hub import Hub

from lyra.adapters import discord_audio  # noqa: I001
from lyra.adapters._shared import ATTACHMENT_EXTS_BASE, resolve_msg
from lyra.adapters.discord_inbound import handle_message
from lyra.adapters.discord_normalize import normalize as _normalize_impl
from lyra.adapters.discord_outbound import (
    _discord_typing_worker,
    send as _send_impl,
    send_streaming as _send_streaming_impl,
)
from lyra.adapters.discord_threads import restore_hot_threads
from lyra.adapters.discord_voice import (
    VoiceSessionManager,
    handle_voice_command as _handle_voice_command_impl,
)
from lyra.core.auth import AuthMiddleware
from lyra.core.trust import TrustLevel
from lyra.core.circuit_breaker import CircuitRegistry
from lyra.core.message import (
    InboundAudio,
    InboundMessage,
    OutboundAttachment,
    OutboundAudio,
    OutboundAudioChunk,
    OutboundMessage,
)
from lyra.core.messages import MessageManager
from lyra.core.thread_store import ThreadStore

# Discord: same base extensions, no platform-specific additions needed.
_ATTACHMENT_EXTS = ATTACHMENT_EXTS_BASE

log = logging.getLogger(__name__)

# Sentinel used when no AuthMiddleware is provided — denies all traffic by default.
_DENY_ALL = AuthMiddleware(store=None, role_map={}, default=TrustLevel.BLOCKED)

# Permissive sentinel for use in tests — allows all traffic as PUBLIC.
_ALLOW_ALL = AuthMiddleware(store=None, role_map={}, default=TrustLevel.PUBLIC)


_AUTO_THREAD_TRUE = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True)
class DiscordConfig:
    token: str = field(repr=False)
    auto_thread: bool = True


def load_discord_config() -> DiscordConfig:
    """Load Discord configuration from environment variables.

    Raises SystemExit if DISCORD_TOKEN is absent. Never logs the token.
    """
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise SystemExit("Missing required env var: DISCORD_TOKEN")
    auto_thread_str = os.environ.get("DISCORD_AUTO_THREAD", "").strip().lower()
    auto_thread = auto_thread_str in _AUTO_THREAD_TRUE if auto_thread_str else True
    return DiscordConfig(token=token, auto_thread=auto_thread)


class DiscordAdapter(discord.Client):
    """Discord channel adapter — discord.py v2 Gateway mode.

    Security contract:
    - Never logs the bot token.
    - All inbound messages produce trust='user' via Message.from_adapter().
    - Bot's own messages are silently discarded.
    """

    def __init__(  # noqa: PLR0913 — DI constructor, each arg is a required dependency
        self,
        hub: "Hub",
        bot_id: str = "main",
        *,
        intents: discord.Intents | None = None,
        circuit_registry: CircuitRegistry | None = None,
        msg_manager: MessageManager | None = None,
        auto_thread: bool = True,
        thread_hot_hours: int = 36,
        auth: AuthMiddleware = _DENY_ALL,
        thread_store: ThreadStore | None = None,
    ) -> None:
        if intents is None:
            intents = discord.Intents.default()
            intents.message_content = True
        super().__init__(intents=intents)
        self._hub = hub
        self._bot_id = bot_id
        self._circuit_registry = circuit_registry
        self._msg_manager = msg_manager
        self._auto_thread = auto_thread
        self._thread_hot_hours = thread_hot_hours
        self._auth: AuthMiddleware = auth
        self._max_audio_bytes: int = int(
            os.environ.get("LYRA_MAX_AUDIO_BYTES", 5 * 1024 * 1024)
        )
        self._typing_tasks: dict[int, asyncio.Task[None]] = {}
        self._bot_user: Any = None  # set on on_ready; None until login
        self._mention_re: re.Pattern[str] | None = None  # compiled on on_ready
        self._owned_threads: set[int] = set()  # populated from ThreadStore on on_ready
        self._thread_store: ThreadStore | None = thread_store
        self._thread_sessions: dict[str, tuple[str, str]] = {}
        self._vsm: VoiceSessionManager = VoiceSessionManager()

    def _msg(self, key: str, fallback: str) -> str:
        """Return a localised message string, falling back when no manager."""
        return resolve_msg(
            self._msg_manager, key, platform="discord", fallback=fallback
        )

    def _start_typing(self, send_to_id: int) -> None:
        """Start (or restart) the typing indicator background task for send_to_id."""
        existing = self._typing_tasks.pop(send_to_id, None)
        if existing and not existing.done():
            existing.cancel()
        self._typing_tasks[send_to_id] = asyncio.create_task(
            _discord_typing_worker(self._resolve_channel, send_to_id),
            name=f"typing:discord:{send_to_id}",
        )

    def _cancel_typing(self, send_to_id: int) -> None:
        """Cancel and remove the typing indicator task for send_to_id."""
        task = self._typing_tasks.pop(send_to_id, None)
        if task and not task.done():
            task.cancel()

    async def close(self) -> None:
        """Cancel pending typing tasks and drain voice sessions before closing."""
        tasks = list(self._typing_tasks.values())
        self._typing_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self._vsm.leave_all()
        await super().close()

    async def on_ready(self) -> None:
        """Cache bot user and compile mention regex on login."""
        self._bot_user = self.user
        if self.user is not None:
            self._mention_re = re.compile(rf"<@!?{self.user.id}>")
        log.info(
            "Discord bot ready: %s (id=%s)",
            self.user,
            getattr(self.user, "id", "?"),
        )
        if not self.intents.message_content:
            log.warning(
                "message_content intent is disabled — "
                "guild message content will be empty. "
                "Enable 'Message Content Intent' in the Developer Portal."
            )
        # Restore hot threads from ThreadStore on startup.
        if self._thread_store is not None:
            try:
                self._owned_threads = await restore_hot_threads(
                    self._thread_store, self._bot_id, self._thread_hot_hours
                )
            except Exception:
                log.exception("ThreadStore: failed to restore owned threads")

    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """Invalidate stale voice session when the bot is forcibly disconnected."""
        bot_user = self._bot_user
        if bot_user is None or member.id != bot_user.id or after.channel is not None:
            return
        # member.guild is always set for voice state events (guild-only, no DM voice).
        guild_id = str(member.guild.id)
        self._vsm.invalidate(guild_id)

    async def _handle_voice_command(
        self, message: Any, trust: TrustLevel = TrustLevel.TRUSTED
    ) -> bool:
        """Detect and handle !join / !join stay / !leave voice commands."""
        return await _handle_voice_command_impl(self, message, trust)

    def normalize_audio(
        self,
        raw: Any,
        audio_bytes: bytes,
        mime_type: str,
        *,
        trust_level: TrustLevel,
    ) -> InboundAudio:
        """Build an InboundAudio envelope from a Discord audio message."""
        return discord_audio.normalize_audio(
            raw,
            audio_bytes,
            mime_type,
            bot_id=self._bot_id,
            trust_level=trust_level,
        )

    def normalize(
        self,
        raw: Any,
        *,
        thread_id: int | None = None,
        channel_id: int | None = None,
        trust_level: TrustLevel = TrustLevel.TRUSTED,
    ) -> InboundMessage:
        """Convert a discord.py Message (or SimpleNamespace) to InboundMessage."""
        return _normalize_impl(
            self,
            raw,
            thread_id=thread_id,
            channel_id=channel_id,
            trust_level=trust_level,
        )

    async def on_message(self, message: Any) -> None:
        """Handle incoming Gateway message — delegates to discord_inbound."""
        await handle_message(self, message)

    async def send(
        self, original_msg: InboundMessage, outbound: OutboundMessage
    ) -> None:
        """Send response back to Discord."""
        await _send_impl(self, original_msg, outbound)

    async def send_streaming(
        self,
        original_msg: InboundMessage,
        chunks: AsyncIterator[str],
        outbound: OutboundMessage | None = None,
    ) -> None:
        """Stream response with edit-in-place, debounced at ~1s."""
        await _send_streaming_impl(self, original_msg, chunks, outbound)

    async def render_audio(self, msg: OutboundAudio, inbound: InboundMessage) -> None:
        """Send an OutboundAudio envelope as a Discord voice message."""
        await discord_audio.render_audio(
            msg,
            inbound,
            bot_id=self._bot_id,
            resolve_channel=self._resolve_channel,
            http=self.http,
        )

    async def render_attachment(
        self, msg: OutboundAttachment, inbound: InboundMessage
    ) -> None:
        """Send an OutboundAttachment envelope as a Discord file attachment."""
        await discord_audio.render_attachment(
            msg,
            inbound,
            resolve_channel=self._resolve_channel,
            attachment_exts=_ATTACHMENT_EXTS,
        )

    async def render_audio_stream(
        self,
        chunks: AsyncIterator[OutboundAudioChunk],
        inbound: InboundMessage,
    ) -> None:
        """Buffer streamed audio chunks and send as a single Discord file attachment."""
        await discord_audio.render_audio_stream(chunks, inbound, self.render_audio)

    async def render_voice_stream(
        self,
        chunks: AsyncIterator[OutboundAudioChunk],
        inbound: InboundMessage,
    ) -> None:
        """Route TTS stream to the active Discord voice session for this guild."""
        await discord_audio.render_voice_stream(chunks, inbound, self._vsm)

    async def _resolve_channel(self, channel_id: int) -> discord.abc.Messageable:
        """Get channel from cache or fetch from network."""
        channel = self.get_channel(channel_id)
        if channel is None:
            channel = await self.fetch_channel(channel_id)
        return cast(discord.abc.Messageable, channel)
