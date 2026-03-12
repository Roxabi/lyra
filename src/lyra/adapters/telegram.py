from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import Depends, FastAPI, HTTPException, Request

if TYPE_CHECKING:
    from lyra.core.hub import Hub

from aiogram.enums import ChatAction

from lyra.core.circuit_breaker import CircuitRegistry
from lyra.core.message import (
    GENERIC_ERROR_REPLY,
    Attachment,
    CodeBlock,
    InboundMessage,
    OutboundAudio,
    OutboundMessage,
    Platform,
    RenderContext,
    TelegramContext,
)
from lyra.core.messages import MessageManager

log = logging.getLogger(__name__)

TELEGRAM_MAX_LENGTH = 4096  # Telegram Bot API text message limit


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


@dataclass(frozen=True)
class TelegramConfig:
    token: str
    webhook_secret: str
    bot_username: str


def load_config() -> TelegramConfig:
    """Load Telegram configuration from environment variables.

    Raises SystemExit if required variables are absent.
    Never logs the token value.
    """
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token:
        raise SystemExit("Missing required env var: TELEGRAM_TOKEN")
    secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET")
    if not secret:
        raise SystemExit("Missing required env var: TELEGRAM_WEBHOOK_SECRET")
    bot_username = os.environ.get("TELEGRAM_BOT_USERNAME", "lyra_bot")
    return TelegramConfig(token=token, webhook_secret=secret, bot_username=bot_username)


def _make_verifier(secret: str):
    """Return a FastAPI dependency that validates the Telegram webhook secret token.

    If the configured secret is empty (default), all requests are rejected — this is
    the secure default when no secret is configured.
    """

    async def verify(request: Request) -> None:
        incoming = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not secret or incoming != secret:
            raise HTTPException(status_code=401, detail="Unauthorized")

    return verify


class TelegramAdapter:
    """Telegram channel adapter — aiogram v3 webhook style.

    Security contract:
    - Never logs the bot token.
    - Webhook route is protected by X-Telegram-Bot-Api-Secret-Token validation.
    - All inbound messages produce trust='user' via Message.from_adapter().
    """

    def __init__(
        self,
        bot_id: str,
        token: str,
        hub: Hub,
        bot_username: str = "lyra_bot",
        webhook_secret: str = "",
        circuit_registry: CircuitRegistry | None = None,
        msg_manager: MessageManager | None = None,
    ) -> None:
        self._bot_id = bot_id
        self._token = token  # kept private — never logged
        self._webhook_secret = webhook_secret
        if not self._webhook_secret:
            log.warning(
                "webhook_secret is empty — all webhook requests will be rejected"
            )
        self._bot_username = bot_username
        self._hub: Hub = hub
        self._circuit_registry = circuit_registry
        self._msg_manager = msg_manager
        self._audio_tmp_dir: str | None = os.environ.get("LYRA_AUDIO_TMP") or None
        self._max_audio_bytes: int = int(
            os.environ.get("LYRA_MAX_AUDIO_BYTES", 5 * 1024 * 1024)
        )

        # bot is a public attribute so tests can replace it with AsyncMock.
        # Deferred lazily so tests can assign adapter.bot = AsyncMock() after
        # construction without triggering aiogram token validation at test time.
        self._bot: Any = None
        self._dp: Any = None

        from aiogram import Dispatcher, F

        self._dp = Dispatcher()
        # Voice handler must be registered before the generic text handler so
        # aiogram routes voice/audio updates here rather than _on_message.
        self._dp.message.register(
            self._on_voice_message, F.voice | F.audio | F.video_note
        )
        self._dp.message.register(self._on_message)

        self.app = FastAPI()
        self._register_routes()

    @property
    def bot(self) -> Any:
        """Lazy aiogram Bot instance. Tests replace this with an AsyncMock."""
        if self._bot is None:
            from aiogram import Bot

            self._bot = Bot(token=self._token)
        return self._bot

    @bot.setter
    def bot(self, value: Any) -> None:
        self._bot = value

    @property
    def dp(self) -> Any:
        return self._dp

    def _register_routes(self) -> None:
        verifier = _make_verifier(self._webhook_secret)

        @self.app.post(
            "/webhooks/telegram/{bot_id}",
            dependencies=[Depends(verifier)],
        )
        async def handle_update(bot_id: str, request: Request) -> dict:
            if bot_id != self._bot_id:
                raise HTTPException(status_code=404, detail="Not Found")
            from aiogram.types import Update

            body = await request.json()
            update = Update.model_validate(body)
            await self._dp.feed_update(self.bot, update)
            log.debug("Dispatched update for bot_id=%s", bot_id)
            return {"ok": True}

        @self.app.get("/status", dependencies=[Depends(verifier)])
        async def get_status() -> dict:
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if self._circuit_registry is None:
                return {"services": {}, "timestamp": ts}
            all_status = self._circuit_registry.get_all_status()
            return {
                "services": {
                    name: {
                        "state": s.state.value,
                        "retry_after": s.retry_after,
                    }
                    for name, s in all_status.items()
                },
                "timestamp": ts,
            }

    @staticmethod
    def _make_scope_id(chat_id: int, topic_id: int | None) -> str:
        """Build the canonical scope_id for a Telegram chat/topic."""
        if topic_id is not None:
            return f"chat:{chat_id}:topic:{topic_id}"
        return f"chat:{chat_id}"

    def normalize(self, raw: Any) -> InboundMessage:
        """Convert an aiogram Message (or SimpleNamespace) to an InboundMessage.

        Security: trust is always 'user'. normalize() is never called for bot messages.
        Never logs the bot token.
        """
        if raw.from_user is None:
            raise ValueError(
                "normalize() called with no from_user — "
                "service messages must be filtered before normalization"
            )
        chat_type = raw.chat.type
        is_group = chat_type != "private"

        # is_mention is always False in private chats
        is_mention = False
        if is_group and raw.entities:
            for entity in raw.entities:
                if entity.type == "mention":
                    slice_text = raw.text[entity.offset : entity.offset + entity.length]
                    if slice_text == f"@{self._bot_username}":
                        is_mention = True
                        break

        chat_id: int = raw.chat.id
        topic_id: int | None = raw.message_thread_id
        scope_id = self._make_scope_id(chat_id, topic_id)

        text = raw.text or ""
        timestamp = raw.date
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)

        user_id = f"tg:user:{raw.from_user.id}"

        log.debug(
            "Normalizing message from user_id=%s in chat_id=%s",
            user_id,
            chat_id,
        )

        return InboundMessage(
            id=f"telegram:{user_id}:{int(timestamp.timestamp())}",
            platform="telegram",
            bot_id=self._bot_id,
            scope_id=scope_id,
            user_id=user_id,
            user_name=raw.from_user.full_name,
            is_mention=is_mention,
            text=text,
            text_raw=text,
            timestamp=timestamp,
            trust="user",
            platform_meta={
                "chat_id": chat_id,
                "topic_id": topic_id,
                "message_id": getattr(raw, "message_id", None),
                "is_group": is_group,
            },
        )

    async def _download_audio(
        self, file_id: str, duration: int | None = None
    ) -> tuple[Path, float | None]:
        """Download a Telegram audio/voice file to a local temp file.

        Checks file size against LYRA_MAX_AUDIO_BYTES before downloading.
        Cleans up the temp file if the download fails.

        Returns (path, duration_seconds). Caller is responsible for cleanup.
        """
        file_ = await self.bot.get_file(file_id)
        if file_.file_size is not None and file_.file_size > self._max_audio_bytes:
            log.warning(
                "Audio file_id=%s rejected: %d bytes exceeds %d byte limit",
                file_id,
                file_.file_size,
                self._max_audio_bytes,
            )
            raise ValueError(
                f"Audio file too large: "
                f"{file_.file_size} > {self._max_audio_bytes} bytes"
            )
        _, tmp_str = tempfile.mkstemp(suffix=".ogg", dir=self._audio_tmp_dir)
        tmp_path = Path(tmp_str)
        try:
            await self.bot.download(file=file_id, destination=tmp_str)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
        log.debug("Downloaded audio file_id=%s to %s", file_id, tmp_path)
        return tmp_path, float(duration) if duration is not None else None

    async def _on_voice_message(self, msg) -> None:
        """Handle an incoming voice or audio message.

        Sends a typing indicator immediately, downloads the audio to a temp
        file, normalises to MessageType.AUDIO, and pushes to the hub.

        Security: same circuit-guard and trust rules as _on_message.
        Temp file cleanup: if the hub queue is full or circuit is open, we
        delete the temp file here. Otherwise the agent (process()) owns cleanup
        per ADR-013.
        """
        if not msg.from_user or getattr(msg.from_user, "is_bot", False):
            return

        await self.bot.send_chat_action(chat_id=msg.chat.id, action=ChatAction.TYPING)

        voice = msg.voice or msg.audio or msg.video_note
        file_id: str = voice.file_id
        duration: int | None = getattr(voice, "duration", None)

        try:
            tmp_path, duration_seconds = await self._download_audio(file_id, duration)
        except Exception:
            log.exception("Failed to download audio file_id=%s", file_id)
            return

        timestamp = msg.date
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)

        _chat_id: int = msg.chat.id
        _topic_id: int | None = msg.message_thread_id
        _scope_id = self._make_scope_id(_chat_id, _topic_id)
        _user_id = f"tg:user:{msg.from_user.id}"

        hub_msg = InboundMessage(
            id=f"telegram:{_user_id}:{int(timestamp.timestamp())}",
            platform="telegram",
            bot_id=self._bot_id,
            scope_id=_scope_id,
            user_id=_user_id,
            user_name=msg.from_user.full_name,
            is_mention=False,
            text="",
            text_raw="",
            attachments=[
                Attachment(
                    type="audio",
                    url_or_bytes=str(tmp_path),
                    mime_type="audio/ogg",
                    filename=None,
                )
            ],
            timestamp=timestamp,
            trust="user",
            platform_meta={
                "chat_id": _chat_id,
                "topic_id": _topic_id,
                "message_id": getattr(msg, "message_id", None),
                "is_group": msg.chat.type != "private",
            },
        )

        await self._push_to_hub(
            hub_msg, on_drop=lambda: tmp_path.unlink(missing_ok=True)
        )

    async def _push_to_hub(
        self,
        hub_msg: InboundMessage,
        on_drop: Callable[[], None] | None = None,
    ) -> None:
        """Put hub_msg on the inbound bus with circuit-open and backpressure guards.

        on_drop is called before early return in both circuit-open and QueueFull
        cases (e.g. to clean up a temp audio file). Always returns normally so
        aiogram receives HTTP 200.
        """
        if self._circuit_registry is not None:
            cb = self._circuit_registry.get("hub")
            if cb is not None and cb.is_open():
                log.warning(
                    '{"event": "hub_circuit_open", "platform": "telegram",'
                    ' "user_id": "%s", "dropped": true}',
                    hub_msg.user_id,
                )
                if on_drop is not None:
                    on_drop()
                return

        try:
            self._hub.inbound_bus.put(Platform.TELEGRAM, hub_msg)
        except asyncio.QueueFull:
            if on_drop is not None:
                on_drop()
            text = (
                self._msg_manager.get("backpressure_ack", platform="telegram")
                if self._msg_manager
                else "Processing your request\u2026"
            )
            chat_id = hub_msg.platform_meta.get("chat_id")
            if chat_id is None:
                raise ValueError(
                    "platform_meta missing required key 'chat_id' for backpressure ack"
                )
            await self.bot.send_message(chat_id, text)

    async def _on_message(self, msg) -> None:
        """Handle an incoming aiogram message: apply backpressure and put on bus."""
        if not msg.from_user or getattr(msg.from_user, "is_bot", False):
            return

        hub_msg = self.normalize(msg)
        # IMPORTANT: Always return normally to aiogram — webhook must return
        # {"ok": True} (HTTP 200). Never raise here or Telegram will retry
        # the update indefinitely.
        await self._push_to_hub(hub_msg)

    async def send(self, original_msg: InboundMessage, outbound: OutboundMessage) -> None:
        """Send a response back to Telegram via bot.send_message.

        Circuit breaker checks and recording are handled by OutboundDispatcher,
        not here. This method performs the bare send and raises on failure.
        """
        if original_msg.platform != "telegram":
            log.error(
                "send() called with non-telegram message id=%s",
                original_msg.id,
            )
            return
        chat_id: int | None = original_msg.platform_meta.get("chat_id")
        if chat_id is None:
            raise ValueError(
                "platform_meta missing required key 'chat_id' for send()"
            )

        text = _outbound_to_text(outbound)
        sent = await self.bot.send_message(chat_id=chat_id, text=text)
        # Store for session persistence (#67) and reply-to-resume (#83).
        outbound.metadata["reply_message_id"] = sent.message_id

    async def send_streaming(
        self, original_msg: InboundMessage, chunks: AsyncIterator[str]
    ) -> None:
        """Stream response with edit-in-place, debounced at ~500ms.

        Circuit breaker checks and recording are handled by OutboundDispatcher,
        not here. This method performs the bare streaming send and raises on failure.

        TODO: store placeholder.message_id in response.metadata["reply_message_id"]
        once send_streaming() receives a Response argument (#67).
        """
        if original_msg.platform != "telegram":
            log.error(
                "send_streaming() called with non-telegram message id=%s",
                original_msg.id,
            )
            return
        chat_id: int | None = original_msg.platform_meta.get("chat_id")
        if chat_id is None:
            raise ValueError(
                "platform_meta missing required key 'chat_id' for send_streaming()"
            )

        accumulated = ""

        # Send placeholder
        _placeholder_text = (
            self._msg_manager.get("stream_placeholder", platform="telegram")
            if self._msg_manager
            else "\u2026"
        )
        try:
            placeholder = await self.bot.send_message(
                chat_id=chat_id, text=_placeholder_text
            )
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
                if now - last_edit >= 0.5:
                    await self.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=placeholder.message_id,
                        text=accumulated[:TELEGRAM_MAX_LENGTH],
                    )
                    last_edit = now
        except Exception as exc:
            stream_error = exc
            log.exception("Stream interrupted")
            if accumulated:
                suffix = (
                    self._msg_manager.get("stream_interrupted", platform="telegram")
                    if self._msg_manager
                    else " [response interrupted]"
                )
                accumulated += suffix
            else:
                accumulated = (
                    self._msg_manager.get("generic", platform="telegram")
                    if self._msg_manager
                    else GENERIC_ERROR_REPLY
                )

        # Final edit with complete text (always runs, even after stream error)
        if accumulated:
            try:
                await self.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=placeholder.message_id,
                    text=accumulated[:TELEGRAM_MAX_LENGTH],
                )
            except Exception:
                log.exception("Final edit failed")

        # Re-raise stream error so OutboundDispatcher can record CB failure
        if stream_error is not None:
            raise stream_error

    async def render_audio(self, msg: OutboundAudio, ctx: RenderContext) -> None:
        """Send an OutboundAudio envelope as a Telegram voice note (ogg/opus).

        Uses bot.send_voice() with a BytesIO buffer — no temp file required.
        caption (if set) is attached to the voice message.
        reply_to_message_id is derived from ctx.platform_context.message_id
        unless msg.reply_to_id overrides it explicitly.
        """
        if not isinstance(ctx.platform_context, TelegramContext):
            log.error(
                "render_audio() called with non-TelegramContext for msg id=%s", ctx.id
            )
            return

        tg_ctx = ctx.platform_context

        # Determine reply target: explicit override first, else original message id
        reply_to: int | None = None
        if msg.reply_to_id is not None:
            try:
                reply_to = int(msg.reply_to_id)
            except ValueError:
                log.warning(
                    "render_audio: invalid reply_to_id=%r, ignoring", msg.reply_to_id
                )
        elif tg_ctx.message_id is not None:
            reply_to = tg_ctx.message_id

        duration_sec: int | None = (
            msg.duration_ms // 1000 if msg.duration_ms is not None else None
        )

        audio_buf = BytesIO(msg.audio_bytes)
        audio_buf.name = "voice.ogg"

        kwargs: dict = {
            "chat_id": tg_ctx.chat_id,
            "voice": audio_buf,
        }
        if tg_ctx.topic_id is not None:
            kwargs["message_thread_id"] = tg_ctx.topic_id
        if reply_to is not None:
            kwargs["reply_to_message_id"] = reply_to
        if msg.caption:
            kwargs["caption"] = msg.caption[:1024]
        if duration_sec is not None:
            kwargs["duration"] = duration_sec

        await self.bot.send_voice(**kwargs)
