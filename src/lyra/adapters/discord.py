from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from io import BytesIO
from typing import TYPE_CHECKING, Any, cast

import discord

if TYPE_CHECKING:
    from lyra.core.hub import Hub

from lyra.core.circuit_breaker import CircuitRegistry
from lyra.core.message import (
    GENERIC_ERROR_REPLY,
    CodeBlock,
    DiscordContext,
    InboundMessage,
    OutboundAudio,
    OutboundMessage,
    Platform,
    RenderContext,
)
from lyra.core.messages import MessageManager

log = logging.getLogger(__name__)

DISCORD_MAX_LENGTH = 2000  # Discord API message length limit


def _outbound_to_text(outbound: OutboundMessage) -> str:
    """Flatten OutboundMessage content parts to a plain text string.

    Handles str (plain text) and CodeBlock parts. Attachment parts are
    rendered as their URL. This is the minimal adapter-layer rendering
    used until _render_text / _render_buttons are implemented (Slice V3).
    """
    parts: list[str] = []
    for part in outbound.content:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, CodeBlock):
            lang = part.language or ""
            parts.append(f"```{lang}\n{part.code}\n```")
        else:
            # Attachment
            caption = f" — {part.caption}" if part.caption else ""
            parts.append(f"{part.url}{caption}")
    return "\n".join(parts)
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

    def __init__(
        self,
        hub: "Hub",
        bot_id: str = "main",
        *,
        intents: discord.Intents | None = None,
        circuit_registry: CircuitRegistry | None = None,
        msg_manager: MessageManager | None = None,
        auto_thread: bool = True,
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
        # Set on on_ready; None until login completes. Tests set this directly.
        self._bot_user: Any = None
        # Compiled once in on_ready (requires bot user ID). None until then.
        self._mention_re: re.Pattern[str] | None = None

    async def on_ready(self) -> None:
        """Cache bot user and compile mention regex on login."""
        self._bot_user = self.user
        if self.user is not None:
            self._mention_re = re.compile(rf"<@!?{self.user.id}>")
        log.info(
            "Discord bot ready: %s (id=%s)", self.user, getattr(self.user, "id", "?")
        )
        if not self.intents.message_content:
            log.warning(
                "message_content intent is disabled — "
                "guild message content will be empty. "
                "Enable 'Message Content Intent' in the Discord Developer Portal."
            )

    def normalize(
        self, raw: Any, *, thread_id: int | None = None, channel_id: int | None = None
    ) -> InboundMessage:
        """Convert a discord.py Message (or SimpleNamespace) to an InboundMessage.

        thread_id and channel_id can be pre-resolved by on_message() after auto-thread
        creation. platform_meta["message_id"] is always raw.id (original message,
        never thread.id).
        Security: trust='user' always.
        """
        is_mention = self._bot_user is not None and self._bot_user in raw.mentions

        # Strip @mention prefix so content reaches the agent clean
        text = raw.content
        if is_mention:
            if self._mention_re is None and self._bot_user is not None:
                self._mention_re = re.compile(rf"<@!?{self._bot_user.id}>")
            if self._mention_re:
                text = self._mention_re.sub("", text).strip()

        # Resolve channel routing (pre-resolved by on_message after thread creation)
        resolved_channel_id: int = (
            channel_id if channel_id is not None else raw.channel.id
        )
        resolved_thread_id: int | None = thread_id

        # If no override, check if already in a thread
        if resolved_thread_id is None and isinstance(raw.channel, discord.Thread):
            resolved_thread_id = raw.channel.id

        scope_id = (
            f"thread:{resolved_thread_id}"
            if resolved_thread_id
            else f"channel:{resolved_channel_id}"
        )

        # Detect channel type
        channel_type: str = "text"
        if isinstance(raw.channel, discord.Thread):
            channel_type = "thread"
        elif isinstance(raw.channel, discord.ForumChannel):
            channel_type = "forum"
        elif isinstance(raw.channel, discord.VoiceChannel):
            channel_type = "voice"

        timestamp = raw.created_at

        log.debug(
            "Normalizing discord message id=%s from user_id=dc:user:%s",
            raw.id,
            raw.author.id,
        )

        _display_name = getattr(raw.author, "display_name", None)
        return InboundMessage(
            id=f"discord:dc:user:{raw.author.id}:{int(timestamp.timestamp())}",
            platform="discord",
            bot_id=self._bot_id,
            scope_id=scope_id,
            user_id=f"dc:user:{raw.author.id}",
            user_name=_display_name if _display_name is not None else raw.author.name,
            is_mention=is_mention,
            text=text,
            text_raw=raw.content,
            timestamp=timestamp,
            trust="user",
            platform_meta={
                "guild_id": raw.guild.id if raw.guild else None,
                "channel_id": resolved_channel_id,
                # INVARIANT: always original message id, never thread.id
                "message_id": raw.id,
                "thread_id": resolved_thread_id,
                "channel_type": channel_type,
            },
        )

    async def on_message(self, message: Any) -> None:
        """Handle incoming Gateway message.

        Filters own/bot messages, creates auto-thread before normalization,
        applies backpressure, and enqueues to hub bus.
        """
        # S3: discard bot messages early — before normalization to avoid wasted work.
        # Own-message check uses cached _bot_user; falls back to author.bot pre-ready.
        if message.author.bot:
            return
        if message.author == self._bot_user:
            return

        # Pre-detect mention (needed for auto-thread decision, before normalize)
        _is_mention = self._bot_user is not None and self._bot_user in message.mentions

        # S5: Auto-thread creation BEFORE normalize() (frozen dataclass invariant)
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
                # Keep parent channel_id for fetch_message() in send().
                # resolved_channel_id remains message.channel.id (set above).
            except Exception:
                log.exception(
                    "Failed to create Discord thread for message id=%s", message.id
                )
                # Fall through — process in original channel scope

        try:
            hub_msg = self.normalize(
                message,
                thread_id=resolved_thread_id,
                channel_id=resolved_channel_id,
            )
        except Exception:
            log.exception("Failed to normalize discord message id=%s", message.id)
            return

        # Hub circuit guard
        if self._circuit_registry is not None:
            cb = self._circuit_registry.get("hub")
            if cb is not None and cb.is_open():
                log.warning(
                    '{"event": "hub_circuit_open", "platform": "discord",'
                    ' "user_id": "%s", "dropped": true}',
                    hub_msg.user_id,
                )
                return  # silent drop

        # S5+S6: non-blocking enqueue with backpressure ack on full bus
        try:
            self._hub.inbound_bus.put(Platform.DISCORD, hub_msg)
        except asyncio.QueueFull:
            text = (
                self._msg_manager.get("backpressure_ack", platform="discord")
                if self._msg_manager
                else "Processing your request\u2026"
            )
            await message.reply(text)

    async def send(self, original_msg: InboundMessage, outbound: OutboundMessage) -> None:
        """Send response back to Discord.

        Circuit breaker checks and recording are handled by OutboundDispatcher,
        not here. This method performs the bare send and raises on failure.

        Fetches channel from cache (or network fallback) to avoid storing raw
        discord.py objects in hub domain metadata.
        Uses message.reply() for @-mentions, channel.send() otherwise.
        Content is truncated to Discord's 2000-char limit.
        """
        if original_msg.platform != "discord":
            log.error("send() called with non-discord message id=%s", original_msg.id)
            return

        channel_id: int | None = original_msg.platform_meta.get("channel_id")
        if channel_id is None:
            raise ValueError(
                "platform_meta missing required key 'channel_id' for send()"
            )
        thread_id: int | None = original_msg.platform_meta.get("thread_id")
        # Route to thread when one was auto-created; fall back to parent channel.
        send_to_id: int = thread_id if thread_id is not None else channel_id
        send_channel = self.get_channel(send_to_id)
        if send_channel is None:
            send_channel = await self.fetch_channel(send_to_id)

        content = _outbound_to_text(outbound)[:DISCORD_MAX_LENGTH]

        messageable = cast(discord.abc.Messageable, send_channel)
        if original_msg.is_mention and thread_id is None:
            # No auto-thread: reply to original message in parent channel.
            msg_id: int | None = original_msg.platform_meta.get("message_id")
            if msg_id is None:
                raise ValueError(
                    "platform_meta missing required key 'message_id' for mention reply"
                )
            msg = await messageable.fetch_message(msg_id)
            sent = await msg.reply(content)
        else:
            # Thread exists (send in thread) or non-mention: plain send.
            sent = await messageable.send(content)
        # Store for session persistence (#67) and reply-to-resume (#83).
        outbound.metadata["reply_message_id"] = sent.id
        log.debug(
            "stored reply_message_id=%s for msg_id=%s", sent.id, original_msg.id
        )

    async def send_streaming(
        self, original_msg: InboundMessage, chunks: AsyncIterator[str]
    ) -> None:
        """Stream response with edit-in-place, debounced at ~1s.

        Circuit breaker checks and recording are handled by OutboundDispatcher,
        not here. This method performs the bare streaming send and raises on failure.

        TODO: store placeholder.id in response.metadata["reply_message_id"]
        once send_streaming() receives a Response argument (#67).
        """
        if original_msg.platform != "discord":
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
        channel = self.get_channel(send_to_id)
        if channel is None:
            channel = await self.fetch_channel(send_to_id)

        messageable = cast(discord.abc.Messageable, channel)
        accumulated = ""

        # Send placeholder
        _placeholder_text = (
            self._msg_manager.get("stream_placeholder", platform="discord")
            if self._msg_manager
            else "\u2026"
        )
        try:
            placeholder = await messageable.send(_placeholder_text)
        except Exception:
            log.exception("Failed to send placeholder — falling back to non-streaming")
            async for chunk in chunks:
                accumulated += chunk
            fallback_content = accumulated or _placeholder_text
            await self.send(original_msg, OutboundMessage.from_text(fallback_content))
            return

        last_edit = time.monotonic()
        stream_error: Exception | None = None
        try:
            async for chunk in chunks:
                accumulated += chunk
                now = time.monotonic()
                if now - last_edit >= 1.0:
                    await placeholder.edit(content=accumulated[:DISCORD_MAX_LENGTH])
                    last_edit = now
        except Exception as exc:
            stream_error = exc
            log.exception("Stream interrupted")
            if accumulated:
                suffix = (
                    self._msg_manager.get("stream_interrupted", platform="discord")
                    if self._msg_manager
                    else " [response interrupted]"
                )
                accumulated += suffix
            else:
                accumulated = (
                    self._msg_manager.get("generic", platform="discord")
                    if self._msg_manager
                    else GENERIC_ERROR_REPLY
                )

        # Final edit with complete text (always runs, even after stream error)
        if accumulated:
            try:
                await placeholder.edit(content=accumulated[:DISCORD_MAX_LENGTH])
            except Exception:
                log.exception("Final edit failed")

        # Re-raise stream error so OutboundDispatcher can record CB failure
        if stream_error is not None:
            raise stream_error

    async def render_audio(self, msg: OutboundAudio, ctx: RenderContext) -> None:
        """Send an OutboundAudio envelope as a Discord audio file attachment.

        Sends audio_bytes as a discord.File attachment. caption (if set)
        is passed as the message content alongside the attachment.
        reply_to_id overrides the default reply target
        (ctx.platform_context.message_id).
        """
        if not isinstance(ctx.platform_context, DiscordContext):
            log.error(
                "render_audio() called with non-DiscordContext for msg id=%s", ctx.id
            )
            return

        dc_ctx = ctx.platform_context

        channel = self.get_channel(dc_ctx.channel_id)
        if channel is None:
            channel = await self.fetch_channel(dc_ctx.channel_id)

        messageable = cast(discord.abc.Messageable, channel)

        # Derive filename from mime_type — whitelist to prevent crafted filenames.
        _AUDIO_EXTS = {"ogg", "mp3", "mp4", "mpeg", "opus", "wav", "flac", "aac"}
        raw_ext = msg.mime_type.split("/")[-1] if "/" in msg.mime_type else ""
        ext = raw_ext if raw_ext in _AUDIO_EXTS else "bin"
        filename = f"audio.{ext}"

        audio_buf = BytesIO(msg.audio_bytes)
        attachment = discord.File(fp=audio_buf, filename=filename)

        # Determine message to reply to
        reply_to_id: int | None = None
        if msg.reply_to_id is not None:
            try:
                reply_to_id = int(msg.reply_to_id)
            except ValueError:
                log.warning(
                    "render_audio: invalid reply_to_id=%r, ignoring", msg.reply_to_id
                )
        else:
            reply_to_id = dc_ctx.message_id

        content = (msg.caption or "")[:DISCORD_MAX_LENGTH]

        if reply_to_id is not None:
            try:
                ref_msg = await messageable.fetch_message(reply_to_id)
                await ref_msg.reply(content=content or None, file=attachment)
                return
            except Exception:
                log.warning(
                    "render_audio: could not reply to message_id=%s, sending normally",
                    reply_to_id,
                )

        # Reconstruct discord.File — the BytesIO may be exhausted if the reply
        # attempt above partially consumed the buffer before raising.
        audio_buf.seek(0)
        attachment = discord.File(fp=audio_buf, filename=filename)
        await messageable.send(content=content or None, file=attachment)
