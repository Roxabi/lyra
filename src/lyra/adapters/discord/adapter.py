from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, cast

import discord

from lyra.core.stores.thread_store_protocol import ThreadSession

if TYPE_CHECKING:
    from lyra.adapters.shared._shared_streaming import PlatformCallbacks
    from lyra.adapters.shared.outbound_listener import OutboundListener
    from lyra.core.messaging.bus import Bus
    from lyra.core.stores.thread_store_protocol import ThreadStoreProtocol
    from lyra.infrastructure.stores.turn_store import TurnStore

from lyra.adapters.discord import discord_audio  # noqa: I001
from lyra.adapters.discord import discord_audio_outbound
from lyra.adapters.shared._shared import TypingTaskManager, resolve_msg
from lyra.adapters.discord.discord_inbound import handle_message
from lyra.adapters.discord.discord_normalize import normalize as _normalize_impl
from lyra.adapters.shared._base_outbound import OutboundAdapterBase
from lyra.adapters.discord.discord_outbound import (
    _discord_typing_worker,
    build_streaming_callbacks as _build_streaming_callbacks,
    send as _send_impl,
)
from lyra.adapters.discord.voice.discord_voice import VoiceSessionManager
from lyra.adapters.discord.lifecycle import (
    on_guild_join as _on_guild_join_impl,
    on_ready as _on_ready_impl,
    on_voice_state_update as _on_voice_state_update_impl,
)
from lyra.adapters.discord.voice.discord_voice_commands import (
    handle_voice_command as _handle_voice_command_impl,
    register_voice_app_commands as _register_voice_app_commands,
)
from lyra.core.circuit_breaker import CircuitRegistry
from lyra.core.auth.guard import BlockedGuard, GuardChain
from lyra.core.auth.trust import TrustLevel
from lyra.core.messaging.message import (
    DiscordMeta,
    InboundMessage,
    OutboundAttachment,
    OutboundAudio,
    OutboundAudioChunk,
    OutboundMessage,
)
from lyra.core.messaging.messages import MessageManager

log = logging.getLogger(__name__)


class DiscordAdapter(discord.Client, OutboundAdapterBase):
    """Discord channel adapter — discord.py v2 Gateway mode.

    Security contract:
    - Never logs the bot token.
    - All inbound messages produce trust='user' via Message.from_adapter().
    - Bot's own messages are silently discarded.
    """

    def __init__(  # noqa: PLR0913 — DI constructor, each arg is a required dependency
        self,
        bot_id: str = "main",
        *,
        inbound_bus: "Bus[InboundMessage]",
        intents: discord.Intents | None = None,
        circuit_registry: CircuitRegistry | None = None,
        msg_manager: MessageManager | None = None,
        auto_thread: bool = True,
        thread_hot_hours: int = 36,
        thread_store: ThreadStoreProtocol | None = None,
        watch_channels: frozenset[int] = frozenset(),
        turn_store: "TurnStore | None" = None,
    ) -> None:
        if intents is None:
            intents = discord.Intents.default()
            intents.message_content = True
        super().__init__(intents=intents)
        self.tree = discord.app_commands.CommandTree(self)
        _register_voice_app_commands(self.tree, self)
        self._inbound_bus = inbound_bus
        self._bot_id = bot_id
        self._circuit_registry = circuit_registry
        self._msg_manager = msg_manager
        self._auto_thread = auto_thread
        self._thread_hot_hours = thread_hot_hours
        self._guard_chain: GuardChain = GuardChain([BlockedGuard()])
        self._max_audio_bytes: int = int(
            os.environ.get("LYRA_MAX_AUDIO_BYTES", 5 * 1024 * 1024)
        )
        self._typing = TypingTaskManager()
        self._bot_user: Any = None  # set on on_ready; None until login
        self._mention_re: re.Pattern[str] | None = None  # compiled on on_ready
        self._owned_threads: set[int] = set()  # populated from ThreadStore on on_ready
        self._thread_store: ThreadStoreProtocol | None = thread_store
        self._turn_store: "TurnStore | None" = turn_store
        self._watch_channels: frozenset[int] = watch_channels
        self._thread_sessions: dict[str, ThreadSession] = {}
        self._vsm: VoiceSessionManager = VoiceSessionManager()
        self._outbound_listener: "OutboundListener | None" = None
        # Injectable identity resolver for slash command trust (set by wiring layer).
        # Falls back to PUBLIC trust when not set (standalone/test mode).
        self._resolve_identity_fn: Any = None

    def _msg(self, key: str, fallback: str) -> str:
        """Return a localised message string, falling back when no manager."""
        return resolve_msg(
            self._msg_manager, key, platform="discord", fallback=fallback
        )

    @property
    def _typing_tasks(self) -> dict[int, asyncio.Task[None]]:
        """Expose the internal task dict — used by tests and outbound submodules."""
        return self._typing._tasks

    def _start_typing(self, scope_id: int) -> None:
        """Start (or restart) the typing indicator background task for scope_id."""
        self._typing.start(
            scope_id,
            lambda: _discord_typing_worker(self._resolve_channel, scope_id),
        )

    def _cancel_typing(self, scope_id: int) -> None:
        """Cancel and remove the typing indicator task for scope_id."""
        self._typing.cancel(scope_id)

    def _cancel_typing_for(self, inbound: InboundMessage) -> None:
        """Cancel the typing indicator for the channel/thread of *inbound*."""
        if not isinstance(inbound.platform_meta, DiscordMeta):
            return
        channel_id: int = inbound.platform_meta.channel_id
        thread_id: int | None = inbound.platform_meta.thread_id
        send_to_id = thread_id if thread_id is not None else (channel_id or None)
        if send_to_id is not None:
            self._cancel_typing(send_to_id)

    async def astart(self) -> None:
        """Start the outbound listener if wired (NATS mode only)."""
        if self._outbound_listener is not None:
            await self._outbound_listener.start()

    async def close(self) -> None:
        """Cancel typing tasks, drain voice, close ThreadStore, stop listener."""
        await self._typing.cancel_all()
        await self._vsm.leave_all()
        if self._outbound_listener is not None:
            await self._outbound_listener.stop()
        # Close adapter-owned ThreadStore
        if self._thread_store is not None:
            try:
                await self._thread_store.close()
            except Exception:
                log.exception("Failed to close ThreadStore for bot %s", self._bot_id)
        await super().close()

    async def on_ready(self) -> None:
        """Cache bot user and compile mention regex on login."""
        await _on_ready_impl(self)

    async def on_guild_join(self, guild: discord.Guild) -> None:
        """Sync app_commands tree when the bot joins a new guild."""
        await _on_guild_join_impl(self, guild)

    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """Invalidate stale voice session when the bot is forcibly disconnected."""
        await _on_voice_state_update_impl(self, member, before, after)

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
    ) -> InboundMessage:
        """Build an InboundMessage (modality='voice') from a Discord audio message."""
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
        is_admin: bool = False,
    ) -> InboundMessage:
        """Convert a discord.py Message (or SimpleNamespace) to InboundMessage."""
        return _normalize_impl(
            self,
            raw,
            thread_id=thread_id,
            channel_id=channel_id,
            trust_level=trust_level,
            is_admin=is_admin,
        )

    async def on_message(self, message: Any) -> None:
        """Handle incoming Gateway message — delegates to discord_inbound."""
        await handle_message(self, message)

    async def send(
        self, original_msg: InboundMessage, outbound: OutboundMessage
    ) -> None:
        """Send response back to Discord."""
        await _send_impl(self, original_msg, outbound)

    def _make_streaming_callbacks(
        self,
        original_msg: InboundMessage,
        outbound: OutboundMessage | None,
    ) -> "PlatformCallbacks":
        """Build platform-specific callbacks for StreamingSession."""
        return _build_streaming_callbacks(self, original_msg, outbound)

    async def render_audio(self, msg: OutboundAudio, inbound: InboundMessage) -> None:
        """Send an OutboundAudio envelope as a Discord voice message."""
        await discord_audio_outbound.render_audio(self, msg, inbound)
        self._cancel_typing_for(inbound)

    async def render_attachment(
        self, msg: OutboundAttachment, inbound: InboundMessage
    ) -> None:
        """Send an OutboundAttachment envelope as a Discord file attachment."""
        await discord_audio_outbound.render_attachment(self, msg, inbound)
        self._cancel_typing_for(inbound)

    async def render_audio_stream(
        self,
        chunks: AsyncIterator[OutboundAudioChunk],
        inbound: InboundMessage,
    ) -> None:
        """Buffer streamed audio chunks and send as a single Discord file attachment."""
        await discord_audio_outbound.render_audio_stream(self, chunks, inbound)
        self._cancel_typing_for(inbound)

    async def render_voice_stream(
        self,
        chunks: AsyncIterator[OutboundAudioChunk],
        inbound: InboundMessage,
    ) -> None:
        """Route TTS stream to the active Discord voice session for this guild."""
        await discord_audio_outbound.render_voice_stream(self, chunks, inbound)
        self._cancel_typing_for(inbound)

    async def _resolve_channel(self, channel_id: int) -> discord.abc.Messageable:
        """Get channel from cache or fetch from network."""
        channel = self.get_channel(channel_id)
        if channel is None:
            channel = await self.fetch_channel(channel_id)
        return cast(discord.abc.Messageable, channel)
