from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import AsyncIterator
from functools import partial
from typing import TYPE_CHECKING, Any, cast

import discord

from lyra.core.stores.thread_store_protocol import ThreadSession

if TYPE_CHECKING:
    from lyra.adapters.shared.outbound_listener import OutboundListener
    from lyra.core.messaging.bus import Bus
    from lyra.core.ports.blobstore import BlobStorePort
    from lyra.core.stores import TurnStoreProtocol
    from lyra.core.stores.thread_store_protocol import ThreadStoreProtocol
    from lyra.inbound.attachment_ingest import IngestCtx
    from lyra.outbound.emitter import OutboundEmitter

from lyra.adapters.discord import discord_audio  # noqa: I001 — DEBT:module-level-patch-fixtures
from lyra.adapters.discord import discord_audio_outbound
from lyra.adapters.shared._shared import TypingTaskManager, resolve_msg
from lyra.typing import make_typing_factory
from lyra.adapters.discord.discord_inbound import handle_message
from lyra.adapters.discord.discord_normalize import (
    NormalizeDeps,
    normalize as _normalize_impl,
)
from lyra.adapters.shared._base_outbound import OutboundAdapterBase
from lyra.adapters.discord.discord_outbound import (
    _discord_typing_worker,
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
from lyra.core.lifecycle.circuit_breaker import CircuitRegistry
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


# ── Typing plane (#1376) — module-level resolver for AC8 ─────────────────
import uuid  # noqa: E402

from lyra.core.trace import TraceContext  # noqa: E402
from lyra.transport.typing_publisher import is_typing_enabled  # noqa: E402
from lyra.transport.work_scope import WorkScope  # noqa: E402


def _discord_scope_resolver(scope: WorkScope) -> int:
    """Resolve WorkScope → Discord parent channel.id.

    AC8: module-level (not closure over adapter instance) so import-only
    tests can verify without instantiating DiscordAdapter. WorkScope.scope_id
    IS the parent channel.id by T2 construction (inbound captures
    channel.id pre-pre-session-hook — auto-thread cannot drift the key).
    """
    return scope.scope_id


class DiscordAdapter(discord.Client, OutboundAdapterBase):
    """Discord channel adapter — discord.py v2 Gateway mode.

    Security contract:
    - Never logs the bot token.
    - All inbound messages produce trust='user' via Message.from_adapter().
    - Bot's own messages are silently discarded.
    """

    def __init__(  # noqa: PLR0913 — DEBT:wiring-bootstrap-deps
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
        turn_store: "TurnStoreProtocol | None" = None,
        blob_store: "BlobStorePort | None" = None,
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
        self._factory_builder = make_typing_factory(
            partial(_discord_typing_worker, self._resolve_channel)
        )
        self._bot_user: Any = None  # set on on_ready; None until login
        self._mention_re: re.Pattern[str] | None = None  # compiled on on_ready
        self._owned_threads: set[int] = set()  # populated from ThreadStore on on_ready
        self._thread_store: ThreadStoreProtocol | None = thread_store
        self._turn_store: "TurnStoreProtocol | None" = turn_store
        self._blob_store: "BlobStorePort | None" = blob_store
        self._watch_channels: frozenset[int] = watch_channels
        self._thread_sessions: dict[str, ThreadSession] = {}
        self._vsm: VoiceSessionManager = VoiceSessionManager()
        self._outbound_listener: "OutboundListener | None" = None
        # Injectable identity resolver for slash command trust (set by wiring layer).
        # Falls back to PUBLIC trust when not set (standalone/test mode).
        self._resolve_identity_fn: Any = None
        # Post-construction injection: set by bootstrap after build_ingest().
        # None → ingest stage no-ops (CLI / degraded / blobstore unconfigured).
        self._ingest_ctx: "IngestCtx | None" = None

    def _msg(self, key: str, fallback: str) -> str:
        return resolve_msg(
            self._msg_manager, key, platform="discord", fallback=fallback
        )

    @property
    def _typing_tasks(self) -> dict[int, asyncio.Task[None]]:
        """Expose the internal task dict — used by tests and outbound submodules."""
        return self._typing._tasks

    def _start_typing(self, scope_id: int) -> None:
        if is_typing_enabled():
            publisher = getattr(self, "_typing_publisher", None)
            if publisher is not None:
                work_scope = WorkScope(
                    platform="discord",
                    bot_id=self._bot_id,
                    scope_id=scope_id,
                    trace_id=TraceContext.get_trace_id() or uuid.uuid4().hex,
                )
                asyncio.create_task(publisher.publish_started(work_scope))
        else:
            self._typing.start(scope_id, self._factory_builder(scope_id))

    def _cancel_typing(self, scope_id: int) -> None:
        if is_typing_enabled():
            publisher = getattr(self, "_typing_publisher", None)
            if publisher is not None:
                work_scope = WorkScope(
                    platform="discord",
                    bot_id=self._bot_id,
                    scope_id=scope_id,
                    trace_id=TraceContext.get_trace_id() or uuid.uuid4().hex,
                )
                asyncio.create_task(publisher.publish_ended(work_scope))
        else:
            self._typing.cancel(scope_id)

    def _cancel_typing_for(self, inbound: InboundMessage) -> None:
        pm = inbound.platform_meta
        if isinstance(pm, DiscordMeta):
            self._cancel_typing(pm.thread_id or pm.channel_id)

    async def astart(self) -> None:
        if self._outbound_listener is not None:
            await self._outbound_listener.start()

    async def close(self) -> None:
        await self._typing.cancel_all()
        await self._vsm.leave_all()
        if self._outbound_listener is not None:
            await self._outbound_listener.stop()
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
        return await _handle_voice_command_impl(self, message, trust)

    def normalize_audio(
        self,
        raw: Any,
        audio_bytes: bytes,
        mime_type: str,
        *,
        trust_level: TrustLevel,
        pending: "discord_audio.PendingAttachment | None" = None,
    ) -> InboundMessage:
        return discord_audio.normalize_audio(
            raw,
            audio_bytes,
            mime_type,
            bot_id=self._bot_id,
            trust_level=trust_level,
            pending=pending,
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
        return _normalize_impl(
            NormalizeDeps(
                adapter=self,
                raw=raw,
                thread_id=thread_id,
                channel_id=channel_id,
                trust_level=trust_level,
                is_admin=is_admin,
            )
        )

    async def on_message(self, message: Any) -> None:
        """Handle incoming Gateway message — delegates to discord_inbound."""
        await handle_message(self, message)

    async def send(
        self, original_msg: InboundMessage, outbound: OutboundMessage
    ) -> None:
        """Send response back to Discord."""
        await _send_impl(self, original_msg, outbound)

    def _make_emitter(
        self,
        original_msg: InboundMessage,
        outbound: OutboundMessage | None,
    ) -> "OutboundEmitter":
        """Build an OutboundEmitter composed of Discord stages (#1279, S7)."""
        from lyra.adapters.discord.discord_formatter import DiscordFormatter
        from lyra.adapters.discord.discord_formatting import _validate_inbound
        from lyra.adapters.discord.discord_outbound import DiscordTypingIndicator
        from lyra.adapters.shared._emitter import _make_emitter as _shared

        return _shared(
            self,
            original_msg,
            outbound,
            validate=_validate_inbound,
            bad_msg="not a discord message",
            formatter_cls=DiscordFormatter,
            formatter_kwargs_fn=lambda m: {
                "send_to_id": m[1] or m[0],
                "reply_msg_id": m[2],
                "should_reply": m[2] and m[1] is None,
                "original_msg": original_msg,
            },
            typing_cls=DiscordTypingIndicator,
            scope_id_fn=lambda m: m[1] or m[0],
        )

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
        self, chunks: AsyncIterator[OutboundAudioChunk], inbound: InboundMessage
    ) -> None:
        """Buffer streamed audio chunks and send as a single Discord file attachment."""
        await discord_audio_outbound.render_audio_stream(self, chunks, inbound)
        self._cancel_typing_for(inbound)

    async def render_voice_stream(
        self, chunks: AsyncIterator[OutboundAudioChunk], inbound: InboundMessage
    ) -> None:
        """Route TTS stream to the active Discord voice session for this guild."""
        await discord_audio_outbound.render_voice_stream(self, chunks, inbound)
        self._cancel_typing_for(inbound)

    async def _resolve_channel(self, channel_id: int) -> discord.abc.Messageable:
        channel = self.get_channel(channel_id) or await self.fetch_channel(channel_id)
        return cast(discord.abc.Messageable, channel)
