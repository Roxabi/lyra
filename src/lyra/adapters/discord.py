from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from io import BytesIO
from typing import TYPE_CHECKING, Any, cast

import discord

if TYPE_CHECKING:
    from lyra.core.hub import Hub

from lyra.adapters._shared import (
    _AUDIO_EXTS,
    ATTACHMENT_EXTS_BASE,
    _PartialAudioError,
    buffer_audio_chunks,
    parse_reply_to_id,
    push_to_hub_guarded,
    sanitize_filename,
    truncate_caption,
)
from lyra.core.auth import AuthMiddleware, TrustLevel
from lyra.core.circuit_breaker import CircuitRegistry
from lyra.core.message import (
    GENERIC_ERROR_REPLY,
    Attachment,
    InboundAudio,
    InboundMessage,
    OutboundAttachment,
    OutboundAudio,
    OutboundAudioChunk,
    OutboundMessage,
    Platform,
    RoutingContext,
)
from lyra.core.messages import MessageManager

# Discord: same base extensions, no platform-specific additions needed.
_ATTACHMENT_EXTS = ATTACHMENT_EXTS_BASE

log = logging.getLogger(__name__)

DISCORD_MAX_LENGTH = 2000  # Discord API message length limit

# Sentinel used when no AuthMiddleware is provided — denies all traffic by default.
_DENY_ALL = AuthMiddleware(user_map={}, role_map={}, default=TrustLevel.BLOCKED)

# Permissive sentinel for use in tests — allows all traffic as PUBLIC.
_ALLOW_ALL = AuthMiddleware(user_map={}, role_map={}, default=TrustLevel.PUBLIC)


_AUTO_THREAD_TRUE = frozenset({"1", "true", "yes", "on"})

# Accepted audio MIME types for inbound attachment detection.
_AUDIO_MIME_TYPES = frozenset(
    {
        "audio/ogg",
        "audio/mpeg",
        "audio/mp4",
        "audio/opus",
        "audio/wav",
        "audio/flac",
        "audio/aac",
    }
)


# ---------------------------------------------------------------------------
# Typing indicator — two-phase design (mirrors TelegramAdapter pattern)
#
# Phase 1 (message receipt): _start_typing() creates a _discord_typing_worker
#   Task that fires trigger_typing() every 8s (Discord expires after ~10s).
#   Starts immediately when on_message() receives a message, before any
#   processing begins.
#
# Phase 2 (response send): _cancel_typing() stops the task. For both send()
#   and send_streaming() this happens at the top of each method, right before
#   the response is written. This replaces the old `async with channel.typing()`
#   wrapper which only covered the send phase, not the backend processing phase.
#
# Drop safety: if the message is dropped (circuit open or QueueFull), on_drop
#   calls _cancel_typing() so the indicator doesn't spin indefinitely.
# ---------------------------------------------------------------------------
async def _discord_typing_worker(
    resolve_channel: Callable,
    channel_id: int,
) -> None:
    """Hold Discord typing indicator for channel_id until cancelled.

    Uses channel.typing() context manager (discord.py 2.x) which sends the
    indicator immediately and refreshes it automatically. Exits cleanly on
    CancelledError so _cancel_typing() stops it without errors.
    """
    try:
        channel = await resolve_channel(channel_id)
        async with channel.typing():
            await asyncio.sleep(float("inf"))  # hold until cancelled
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        log.debug("discord typing worker for channel %d: %s", channel_id, exc)


def _extract_attachments(raw_attachments: list[Any]) -> list[Attachment]:
    """Extract non-audio Attachment objects from Discord message.attachments."""
    result: list[Attachment] = []
    for a in raw_attachments:
        ct = getattr(a, "content_type", None) or ""
        if ct in _AUDIO_MIME_TYPES:
            continue
        if ct.startswith("image/"):
            att_type = "image"
        elif ct.startswith("video/"):
            att_type = "video"
        else:
            att_type = "file"
        result.append(
            Attachment(
                type=att_type,
                url_or_path_or_bytes=a.url,
                mime_type=ct or "application/octet-stream",
                filename=getattr(a, "filename", None),
            )
        )
    return result


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
        auth: AuthMiddleware = _DENY_ALL,
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
        self._auth: AuthMiddleware = auth
        self._max_audio_bytes: int = int(
            os.environ.get("LYRA_MAX_AUDIO_BYTES", 5 * 1024 * 1024)
        )
        self._typing_tasks: dict[int, asyncio.Task[None]] = {}
        # Set on on_ready; None until login completes. Tests set directly.
        self._bot_user: Any = None
        # Compiled once in on_ready (requires bot user ID). None until then.
        self._mention_re: re.Pattern[str] | None = None
        # Thread IDs created by or claimed by this bot — only this bot responds there.
        self._owned_threads: set[int] = set()

    def _msg(self, key: str, fallback: str) -> str:
        """Return a localised message string, falling back when no manager."""
        return (
            self._msg_manager.get(key, platform="discord")
            if self._msg_manager
            else fallback
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
        """Cancel all pending typing indicator tasks before closing the client."""
        tasks = list(self._typing_tasks.values())
        self._typing_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
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

    def normalize_audio(
        self,
        raw: Any,
        audio_bytes: bytes,
        mime_type: str,
        *,
        trust_level: TrustLevel = TrustLevel.TRUSTED,
    ) -> InboundAudio:
        """Build an InboundAudio envelope from a Discord audio message.

        Security: trust is always 'user'. Bot messages are filtered by
        on_message().
        """
        is_thread = isinstance(raw.channel, discord.Thread)
        scope_id = (
            f"thread:{raw.channel.id}" if is_thread else f"channel:{raw.channel.id}"
        )
        user_id = f"dc:user:{raw.author.id}"
        timestamp = raw.created_at
        platform_meta = {
            "guild_id": raw.guild.id if raw.guild else None,
            "channel_id": raw.channel.id,
            "message_id": raw.id,
        }
        routing = RoutingContext(
            platform=Platform.DISCORD.value,
            bot_id=self._bot_id,
            scope_id=scope_id,
            thread_id=str(raw.channel.id) if is_thread else None,
            reply_to_message_id=str(raw.id),
            platform_meta=dict(platform_meta),
        )
        return InboundAudio(
            id=f"discord:{user_id}:{int(timestamp.timestamp())}:{raw.id}",
            platform=Platform.DISCORD.value,
            bot_id=self._bot_id,
            scope_id=scope_id,
            user_id=user_id,
            audio_bytes=audio_bytes,
            mime_type=mime_type,
            duration_ms=None,
            file_id=None,
            timestamp=timestamp,
            user_name=(getattr(raw.author, "display_name", None) or raw.author.name),
            is_mention=False,
            trust_level=trust_level,
            platform_meta=platform_meta,
            routing=routing,
        )

    def normalize(
        self,
        raw: Any,
        *,
        thread_id: int | None = None,
        channel_id: int | None = None,
        trust_level: TrustLevel = TrustLevel.TRUSTED,
    ) -> InboundMessage:
        """Convert a discord.py Message (or SimpleNamespace) to InboundMessage.

        thread_id and channel_id can be pre-resolved by on_message() after
        auto-thread creation. platform_meta["message_id"] is always raw.id
        (original message, never thread.id).
        Security: trust='user' always.
        """
        is_mention = self._bot_user is not None and self._bot_user in raw.mentions

        # Strip @mention prefix so content reaches the agent clean
        text = raw.content
        if is_mention:
            if self._mention_re is None and self._bot_user is not None:
                self._mention_re = re.compile(rf"<@!?{self._bot_user.id}>")
        if is_mention and self._mention_re:
            text = self._mention_re.sub("", text).strip()

        # Resolve channel routing (pre-resolved by on_message after thread)
        resolved_channel_id: int = (
            channel_id if channel_id is not None else raw.channel.id
        )
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
        attachments = _extract_attachments(getattr(raw, "attachments", None) or [])
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
            bot_id=self._bot_id,
            scope_id=scope_id,
            thread_id=(
                str(resolved_thread_id) if resolved_thread_id is not None else None
            ),
            reply_to_message_id=str(raw.id),
            platform_meta=dict(platform_meta),
        )
        return InboundMessage(
            id=(f"discord:{user_id}:{int(timestamp.timestamp())}:{raw.id}"),
            platform=Platform.DISCORD.value,
            bot_id=self._bot_id,
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
            platform_meta=platform_meta,
            routing=routing,
        )

    async def on_message(self, message: Any) -> None:  # noqa: C901, PLR0915 — gateway dispatch: each message type branch is independent
        """Handle incoming Gateway message.

        Filters own/bot messages, creates auto-thread before normalization,
        applies backpressure, and enqueues to hub bus.
        """
        # Discard bot messages early — before normalization to avoid waste.
        if message.author.bot:
            return

        # Auth gate — runs before normalize() and before audio handling.
        _raw_uid = str(message.author.id)
        roles = (
            [str(r.id) for r in message.author.roles]
            if hasattr(message.author, "roles")
            else []
        )
        trust = self._auth.check(_raw_uid, roles=roles)
        if trust == TrustLevel.BLOCKED:
            log.info(
                "auth_reject user=%s channel=discord", f"dc:user:{message.author.id}"
            )
            return

        # Audio attachment detection
        audio_attachment = next(
            (
                a
                for a in (getattr(message, "attachments", None) or [])
                if getattr(a, "content_type", "") in _AUDIO_MIME_TYPES
            ),
            None,
        )
        if audio_attachment is not None:
            user_id = f"dc:user:{message.author.id}"
            log.info(
                "audio_received",
                extra={
                    "platform": "discord",
                    "user_id": user_id,
                    "message_id": message.id,
                },
            )
            # Pre-download size check (matches Telegram's _download_audio guard)
            att_size = getattr(audio_attachment, "size", None)
            if att_size is not None and att_size > self._max_audio_bytes:
                log.warning(
                    "Audio attachment rejected: %d bytes exceeds %d byte limit"
                    " (message_id=%s)",
                    att_size,
                    self._max_audio_bytes,
                    message.id,
                )
                try:
                    await message.reply(
                        self._msg(
                            "audio_too_large",
                            "That audio file is too large to process.",
                        )
                    )
                except Exception:
                    log.warning(
                        "Failed to send audio-too-large reply for message_id=%s",
                        message.id,
                    )
                return

            try:
                audio_bytes = await audio_attachment.read()
            except Exception:
                log.exception(
                    "Failed to download audio attachment for message_id=%s",
                    message.id,
                )
                return

            hub_audio = self.normalize_audio(
                message,
                audio_bytes=audio_bytes,
                mime_type=getattr(audio_attachment, "content_type", "audio/ogg"),
            )

            async def _send_bp(text: str) -> None:
                await message.reply(text)

            self._start_typing(message.channel.id)
            await push_to_hub_guarded(
                inbound_bus=self._hub.inbound_audio_bus,
                platform=Platform.DISCORD,
                msg=hub_audio,
                circuit_registry=self._circuit_registry,
                on_drop=lambda: self._cancel_typing(message.channel.id),
                send_backpressure=_send_bp,
                get_msg=self._msg,
            )
            return  # audio messages handled separately; skip text path

        # Pre-detect mention (needed for auto-thread decision)
        _is_mention = self._bot_user is not None and self._bot_user in message.mentions

        # In DMs (no guild), always respond.
        # In servers: only respond when directly mentioned or in an owned thread.
        _is_dm = message.guild is None
        _in_owned_thread = (
            isinstance(message.channel, discord.Thread)
            and message.channel.id in self._owned_threads
        )
        if not _is_dm and not _is_mention and not _in_owned_thread:
            return

        # Auto-thread creation BEFORE normalize() (frozen dataclass)
        resolved_thread_id: int | None = None
        resolved_channel_id: int = message.channel.id
        if (
            self._auto_thread
            and _is_mention
            and not isinstance(message.channel, discord.Thread)
            and hasattr(message.channel, "create_thread")
        ):
            try:
                thread = await message.create_thread(
                    name=(
                        f"Chat with {message.author.display_name}"
                        f" ({str(message.author.id)[-4:]})"
                    )[:100].strip()
                )
                resolved_thread_id = thread.id
                self._owned_threads.add(thread.id)
            except Exception:
                log.exception(
                    "Failed to create Discord thread for message id=%s",
                    message.id,
                )

        # Claim an existing thread when directly mentioned inside it.
        if _is_mention and isinstance(message.channel, discord.Thread):
            self._owned_threads.add(message.channel.id)

        try:
            hub_msg = self.normalize(
                message,
                thread_id=resolved_thread_id,
                channel_id=resolved_channel_id,
                trust_level=trust,
            )
        except Exception:
            log.exception("Failed to normalize discord message id=%s", message.id)
            return

        log.info(
            "message_received",
            extra={
                "platform": "discord",
                "user_id": hub_msg.user_id,
                "scope_id": hub_msg.scope_id,
                "msg_id": hub_msg.id,
            },
        )

        send_to_id: int = (
            resolved_thread_id
            if resolved_thread_id is not None
            else resolved_channel_id
        )
        self._start_typing(send_to_id)
        await self._push_to_hub(
            hub_msg,
            source_message=message,
            on_drop=lambda: self._cancel_typing(send_to_id),
        )

    async def _push_to_hub(
        self,
        hub_msg: InboundMessage,
        source_message: Any = None,
        on_drop: Callable[[], None] | None = None,
    ) -> None:
        """Put hub_msg on the inbound bus with circuit-open and backpressure guards.

        on_drop is called before early return in both circuit-open and QueueFull
        cases. Always returns normally.
        """

        async def _send_bp(text: str) -> None:
            if source_message is not None:
                await source_message.reply(text)

        await push_to_hub_guarded(
            inbound_bus=self._hub.inbound_bus,
            platform=Platform.DISCORD,
            msg=hub_msg,
            circuit_registry=self._circuit_registry,
            on_drop=on_drop,
            send_backpressure=_send_bp,
            get_msg=self._msg,
        )

    async def _resolve_channel(self, channel_id: int) -> discord.abc.Messageable:
        """Get channel from cache or fetch from network."""
        channel = self.get_channel(channel_id)
        if channel is None:
            channel = await self.fetch_channel(channel_id)
        return cast(discord.abc.Messageable, channel)

    def _render_text(self, text: str) -> list[str]:
        """Split text into <=2000-char chunks (Discord limit)."""
        if not text:
            return []
        return [
            text[i : i + DISCORD_MAX_LENGTH]
            for i in range(0, len(text), DISCORD_MAX_LENGTH)
        ]

    def _render_buttons(self, buttons: list) -> discord.ui.View | None:
        """Convert list[Button] to discord.ui.View, or None if empty."""
        if not buttons:
            return None
        view = discord.ui.View()
        for b in buttons:
            view.add_item(discord.ui.Button(label=b.text, custom_id=b.callback_data))
        return view

    async def send(  # noqa: C901 — attachment loop adds branches
        self, original_msg: InboundMessage, outbound: OutboundMessage
    ) -> None:
        """Send response back to Discord.

        Circuit breaker checks and recording are handled by
        OutboundDispatcher, not here.
        """
        if original_msg.platform != Platform.DISCORD.value:
            log.error(
                "send() called with non-discord message id=%s",
                original_msg.id,
            )
            return

        channel_id: int | None = original_msg.platform_meta.get("channel_id")
        if channel_id is None:
            raise ValueError(
                "platform_meta missing required key 'channel_id' for send()"
            )
        thread_id: int | None = original_msg.platform_meta.get("thread_id")
        send_to_id: int = thread_id if thread_id is not None else channel_id
        messageable = await self._resolve_channel(send_to_id)

        self._cancel_typing(send_to_id)
        text = outbound.to_text()
        chunks = self._render_text(text)
        view = self._render_buttons(outbound.buttons)
        last_idx = len(chunks) - 1

        for i, chunk in enumerate(chunks):
            chunk_view = view if (i == last_idx and view is not None) else None
            if original_msg.is_mention and thread_id is None and i == 0:
                msg_id: int | None = original_msg.platform_meta.get("message_id")
                if msg_id is None:
                    raise ValueError(
                        "platform_meta missing required key"
                        " 'message_id' for mention reply"
                    )
                msg_obj = messageable.get_partial_message(msg_id)  # type: ignore[attr-defined]
                if chunk_view is not None:
                    sent = await msg_obj.reply(chunk, view=chunk_view)
                else:
                    sent = await msg_obj.reply(chunk)
            else:
                if chunk_view is not None:
                    sent = await messageable.send(chunk, view=chunk_view)
                else:
                    sent = await messageable.send(chunk)
            if i == last_idx:
                outbound.metadata["reply_message_id"] = sent.id
        log.debug(
            "stored reply_message_id=%s for msg_id=%s",
            outbound.metadata.get("reply_message_id"),
            original_msg.id,
        )

    async def send_streaming(  # noqa: C901, PLR0915 — streaming protocol: edit/chunk/finalize branches are inherently sequential
        self,
        original_msg: InboundMessage,
        chunks: AsyncIterator[str],
        outbound: OutboundMessage | None = None,
    ) -> None:
        """Stream response with edit-in-place, debounced at ~1s.

        Circuit breaker checks and recording are handled by
        OutboundDispatcher, not here.

        When *outbound* is provided, ``outbound.metadata["reply_message_id"]``
        is set to the placeholder message ID after it is sent.
        """
        if original_msg.platform != Platform.DISCORD.value:
            log.error(
                "send_streaming() called with non-discord message id=%s",
                original_msg.id,
            )
            return

        channel_id: int | None = original_msg.platform_meta.get("channel_id")
        if channel_id is None:
            raise ValueError(
                "platform_meta missing required key 'channel_id' for send_streaming()"
            )
        thread_id: int | None = original_msg.platform_meta.get("thread_id")
        send_to_id: int = thread_id if thread_id is not None else channel_id
        messageable = await self._resolve_channel(send_to_id)

        # The typing task was started by _start_typing() in on_message() on receipt.
        # Cancel it now — the placeholder message is the first visible content.
        # _cancel_typing is a no-op if the task was already done or never started.
        self._cancel_typing(send_to_id)
        parts: list[str] = []

        # Send placeholder
        _placeholder_text = self._msg("stream_placeholder", "\u2026")
        try:
            placeholder = await messageable.send(_placeholder_text)
            if outbound is not None:
                outbound.metadata["reply_message_id"] = placeholder.id
        except Exception:
            log.exception("Failed to send placeholder — falling back to non-streaming")
            async for chunk in chunks:
                parts.append(chunk)
            fallback_content = "".join(parts) or _placeholder_text
            fallback_outbound = OutboundMessage.from_text(fallback_content)
            await self.send(original_msg, fallback_outbound)
            if outbound is not None:
                outbound.metadata["reply_message_id"] = fallback_outbound.metadata.get(
                    "reply_message_id"
                )
            return

        last_edit = time.monotonic()
        stream_error: Exception | None = None
        try:
            async for chunk in chunks:
                parts.append(chunk)
                now = time.monotonic()
                if now - last_edit >= 1.0:
                    accumulated = "".join(parts)
                    await placeholder.edit(content=accumulated[:DISCORD_MAX_LENGTH])
                    last_edit = now
        except Exception as exc:
            stream_error = exc
            log.exception("Stream interrupted")

        accumulated = "".join(parts)
        if stream_error is not None:
            if accumulated:
                accumulated += self._msg(
                    "stream_interrupted", " [response interrupted]"
                )
            else:
                accumulated = self._msg("generic", GENERIC_ERROR_REPLY)

        # Final edit with complete text (always runs, even after error)
        if accumulated:
            try:
                await placeholder.edit(content=accumulated[:DISCORD_MAX_LENGTH])
            except Exception:
                log.exception("Final edit failed")

        # Re-raise stream error so OutboundDispatcher can record CB failure
        if stream_error is not None:
            raise stream_error

    async def render_audio(self, msg: OutboundAudio, inbound: InboundMessage) -> None:
        """Send an OutboundAudio envelope as a Discord audio file attachment.

        Sends audio_bytes as a discord.File attachment. caption (if set)
        is passed as the message content alongside the attachment.
        reply_to_id overrides the default reply target
        (inbound.platform_meta["message_id"]).
        """
        if inbound.platform != Platform.DISCORD.value:
            log.error(
                "render_audio() called with non-discord message id=%s",
                inbound.id,
            )
            return

        channel_id: int | None = inbound.platform_meta.get("channel_id")
        if channel_id is None:
            log.error(
                "render_audio: platform_meta missing 'channel_id' for msg id=%s",
                inbound.id,
            )
            return

        thread_id: int | None = inbound.platform_meta.get("thread_id")
        send_to_id = thread_id if thread_id is not None else channel_id
        messageable = await self._resolve_channel(send_to_id)

        # Derive filename from mime_type — whitelist to prevent crafted names.
        raw_ext = msg.mime_type.split("/")[-1] if "/" in msg.mime_type else ""
        ext = raw_ext if raw_ext in _AUDIO_EXTS else "bin"
        filename = f"audio.{ext}"

        audio_buf = BytesIO(msg.audio_bytes)
        attachment = discord.File(fp=audio_buf, filename=filename)

        # Determine message to reply to
        message_id: int | None = inbound.platform_meta.get("message_id")
        reply_to_id = parse_reply_to_id(msg.reply_to_id)
        if reply_to_id is None:
            reply_to_id = message_id

        content = (msg.caption or "")[:DISCORD_MAX_LENGTH]

        if reply_to_id is not None:
            try:
                ref_msg = await messageable.fetch_message(reply_to_id)  # type: ignore[attr-defined]
                await ref_msg.reply(content=content or None, file=attachment)
                return
            except Exception:
                log.warning(
                    "render_audio: could not reply to message_id=%s, sending normally",
                    reply_to_id,
                )

        # Construct a fresh discord.File — the previous BytesIO may have been
        # partially consumed by the failed reply attempt above.
        attachment = discord.File(fp=BytesIO(msg.audio_bytes), filename=filename)
        await messageable.send(content=content or None, file=attachment)

    async def render_attachment(
        self, msg: OutboundAttachment, inbound: InboundMessage
    ) -> None:
        """Send an OutboundAttachment envelope as a Discord file attachment.

        Wraps data in discord.File and sends via messageable.send() or msg.reply().
        Caption (if set) is passed as message content. Reply and thread routing
        follow the same pattern as render_audio.
        """
        if inbound.platform != Platform.DISCORD.value:
            log.error(
                "render_attachment() called with non-discord message id=%s",
                inbound.id,
            )
            return

        channel_id: int | None = inbound.platform_meta.get("channel_id")
        if channel_id is None:
            log.error(
                "render_attachment: platform_meta missing 'channel_id' for msg id=%s",
                inbound.id,
            )
            return

        thread_id: int | None = inbound.platform_meta.get("thread_id")
        send_to_id = thread_id if thread_id is not None else channel_id
        messageable = await self._resolve_channel(send_to_id)

        # Derive filename: sanitize explicit name or derive from mime_type.
        if msg.filename:
            filename = sanitize_filename(
                msg.filename,
                _ATTACHMENT_EXTS,
            )
        else:
            raw_ext = msg.mime_type.split("/")[-1] if "/" in msg.mime_type else ""
            ext = raw_ext if raw_ext in _ATTACHMENT_EXTS else "bin"
            filename = f"attachment.{ext}"

        buf = BytesIO(msg.data)
        file_obj = discord.File(fp=buf, filename=filename)

        # Determine reply target
        message_id: int | None = inbound.platform_meta.get("message_id")
        reply_to_id = parse_reply_to_id(msg.reply_to_id)
        if reply_to_id is None:
            reply_to_id = message_id

        content = truncate_caption(msg.caption, DISCORD_MAX_LENGTH) or ""

        if reply_to_id is not None:
            try:
                ref_msg = await messageable.fetch_message(reply_to_id)  # type: ignore[attr-defined]
                await ref_msg.reply(content=content or None, file=file_obj)
                return
            except Exception:
                log.warning(
                    "render_attachment: could not reply to"
                    " message_id=%s, sending normally",
                    reply_to_id,
                )

        # Fallback: construct fresh discord.File (previous BytesIO may be consumed).
        file_obj = discord.File(fp=BytesIO(msg.data), filename=filename)
        await messageable.send(content=content or None, file=file_obj)

    async def render_audio_stream(
        self,
        chunks: AsyncIterator[OutboundAudioChunk],
        inbound: InboundMessage,
    ) -> None:
        """Buffer streamed audio chunks and send as a single Discord file attachment."""
        if inbound.platform != Platform.DISCORD.value:
            log.error(
                "render_audio_stream() called with non-discord message id=%s",
                inbound.id,
            )
            return

        try:
            assembled = await buffer_audio_chunks(chunks)
        except _PartialAudioError as e:
            await self.render_audio(e.audio, inbound)
            raise e.cause from e
        if assembled is None:
            return
        await self.render_audio(assembled, inbound)
