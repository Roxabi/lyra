from __future__ import annotations

import logging
import os
import re
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

from lyra.adapters._shared import (
    ATTACHMENT_EXTS_BASE,
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
    OutboundMessage,
    Platform,
)
from lyra.core.messages import MessageManager

log = logging.getLogger(__name__)

# Telegram: base extensions + audio (Telegram supports audio via send_document).
_ATTACHMENT_EXTS = ATTACHMENT_EXTS_BASE | frozenset(
    {
        "ogg",
        "mp3",
        "opus",
        "wav",
        "flac",
        "aac",  # audio
    }
)

TELEGRAM_MAX_LENGTH = 4096  # Telegram Bot API text message limit

# Sentinel used when no AuthMiddleware is provided — denies all traffic by default.
_DENY_ALL = AuthMiddleware(user_map={}, role_map={}, default=TrustLevel.BLOCKED)

# Permissive sentinel for use in tests — allows all traffic as PUBLIC.
_ALLOW_ALL = AuthMiddleware(user_map={}, role_map={}, default=TrustLevel.PUBLIC)
_MARKDOWNV2_SPECIAL = re.compile(r"([_*\[\]()~`>#\+\-=|{}.!\\])")


def _extract_attachments(msg: Any) -> list[Attachment]:
    """Extract non-audio Attachment objects from a Telegram message."""
    result: list[Attachment] = []
    # photo: list of PhotoSize, take largest (last)
    if getattr(msg, "photo", None):
        largest = msg.photo[-1]
        result.append(
            Attachment(
                type="image",
                url_or_path_or_bytes=f"tg:file_id:{largest.file_id}",
                mime_type="image/jpeg",
            )
        )
    if getattr(msg, "document", None):
        doc = msg.document
        result.append(
            Attachment(
                type="file",
                url_or_path_or_bytes=f"tg:file_id:{doc.file_id}",
                mime_type=getattr(doc, "mime_type", None) or "application/octet-stream",
                filename=getattr(doc, "file_name", None),
            )
        )
    if getattr(msg, "video", None):
        vid = msg.video
        result.append(
            Attachment(
                type="video",
                url_or_path_or_bytes=f"tg:file_id:{vid.file_id}",
                mime_type=getattr(vid, "mime_type", None) or "video/mp4",
            )
        )
    if getattr(msg, "animation", None):
        anim = msg.animation
        result.append(
            Attachment(
                type="image",
                url_or_path_or_bytes=f"tg:file_id:{anim.file_id}",
                mime_type="image/gif",
            )
        )
    if getattr(msg, "sticker", None):
        sticker = msg.sticker
        # Only static WebP stickers; skip animated (.tgs) and video (.webm)
        if not getattr(sticker, "is_animated", False) and not getattr(
            sticker, "is_video", False
        ):
            result.append(
                Attachment(
                    type="image",
                    url_or_path_or_bytes=f"tg:file_id:{sticker.file_id}",
                    mime_type="image/webp",
                )
            )
    return result


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

    def __init__(  # noqa: PLR0913 — DI constructor, each arg is a required dependency
        self,
        bot_id: str,
        token: str,
        hub: Hub,
        bot_username: str = "lyra_bot",
        webhook_secret: str = "",
        circuit_registry: CircuitRegistry | None = None,
        msg_manager: MessageManager | None = None,
        auth: AuthMiddleware = _DENY_ALL,
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
        self._auth: AuthMiddleware = auth
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

    def _msg(self, key: str, fallback: str) -> str:
        """Return a localised message string, falling back when no manager."""
        return (
            self._msg_manager.get(key, platform="telegram")
            if self._msg_manager
            else fallback
        )

    @staticmethod
    def _make_scope_id(chat_id: int, topic_id: int | None) -> str:
        """Build the canonical scope_id for a Telegram chat/topic."""
        if topic_id is not None:
            return f"chat:{chat_id}:topic:{topic_id}"
        return f"chat:{chat_id}"

    def normalize(
        self, raw: Any, *, trust_level: TrustLevel = TrustLevel.TRUSTED
    ) -> InboundMessage:
        """Convert an aiogram Message (or SimpleNamespace) to an InboundMessage.

        Security: trust is always 'user'. normalize() is never called for bot
        messages.  Never logs the bot token.
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

        text = raw.text or getattr(raw, "caption", None) or ""
        timestamp = raw.date
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)

        user_id = f"tg:user:{raw.from_user.id}"

        log.debug(
            "Normalizing message from user_id=%s in chat_id=%s",
            user_id,
            chat_id,
        )

        attachments = _extract_attachments(raw)
        return InboundMessage(
            id=(
                f"telegram:{user_id}:{int(timestamp.timestamp())}"
                f":{getattr(raw, 'message_id', '')}"
            ),
            platform=Platform.TELEGRAM.value,
            bot_id=self._bot_id,
            scope_id=scope_id,
            user_id=user_id,
            user_name=raw.from_user.full_name,
            is_mention=is_mention,
            text=text,
            text_raw=text,
            attachments=attachments,
            timestamp=timestamp,
            trust="user",
            trust_level=trust_level,
            platform_meta={
                "chat_id": chat_id,
                "topic_id": topic_id,
                "message_id": getattr(raw, "message_id", None),
                "is_group": is_group,
            },
        )

    def normalize_audio(
        self,
        raw: Any,
        audio_bytes: bytes,
        mime_type: str,
        *,
        trust_level: TrustLevel = TrustLevel.TRUSTED,
    ) -> InboundAudio:
        """Build an InboundAudio envelope from a Telegram voice/audio/video_note.

        Security: trust is always 'user'. normalize_audio() is never called for
        bot messages. Never logs the bot token.
        """
        if raw.from_user is None:
            raise ValueError(
                "normalize_audio() called with no from_user — "
                "service messages must be filtered before normalization"
            )
        chat_id: int = raw.chat.id
        topic_id: int | None = getattr(raw, "message_thread_id", None)
        scope_id = self._make_scope_id(chat_id, topic_id)
        voice = raw.voice or raw.audio or getattr(raw, "video_note", None)
        duration_ms: int | None = None
        if voice is not None:
            d = getattr(voice, "duration", None)
            if d is not None:
                duration_ms = int(d) * 1000
        file_id: str | None = (
            getattr(voice, "file_id", None) if voice is not None else None
        )
        timestamp = raw.date
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        user_id = f"tg:user:{raw.from_user.id}"
        return InboundAudio(
            id=(f"telegram:{user_id}:{int(timestamp.timestamp())}:{file_id or ''}"),
            platform=Platform.TELEGRAM.value,
            bot_id=self._bot_id,
            scope_id=scope_id,
            user_id=user_id,
            audio_bytes=audio_bytes,
            mime_type=mime_type,
            duration_ms=duration_ms,
            file_id=file_id,
            timestamp=timestamp,
            user_name=raw.from_user.full_name,
            is_mention=False,
            trust_level=trust_level,
            platform_meta={
                "chat_id": chat_id,
                "topic_id": topic_id,
                "message_id": getattr(raw, "message_id", None),
                "is_group": raw.chat.type != "private",
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

        Downloads audio, builds an InboundAudio envelope, and enqueues it
        on the inbound audio bus with backpressure / circuit-open guards.
        """
        if not msg.from_user or getattr(msg.from_user, "is_bot", False):
            return

        uid = str(msg.from_user.id)
        trust = self._auth.check(uid)
        if trust == TrustLevel.BLOCKED:
            log.info("auth_reject user=%s channel=telegram", uid)
            return
        # TODO(#140): pass trust to normalize_audio() when audio bus is wired

        voice = msg.voice or msg.audio or getattr(msg, "video_note", None)
        if voice is None:
            return
        file_id = getattr(voice, "file_id", None)
        if file_id is None:
            return

        user_id = f"tg:user:{msg.from_user.id}"
        scope_id = self._make_scope_id(msg.chat.id, msg.message_thread_id)
        log.info(
            "audio_received",
            extra={
                "platform": "telegram",
                "user_id": user_id,
                "scope_id": scope_id,
            },
        )

        try:
            tmp_path, _duration_s = await self._download_audio(
                file_id, getattr(voice, "duration", None)
            )
        except ValueError:
            # File too large — notify user
            try:
                await self.bot.send_message(
                    chat_id=msg.chat.id,
                    text=self._msg(
                        "audio_too_large",
                        "That audio file is too large to process.",
                    ),
                )
            except Exception:
                log.warning(
                    "Failed to send audio-too-large reply for user_id=%s",
                    user_id,
                )
            return
        except Exception:
            log.exception(
                "Failed to download audio file_id=%r for user_id=%s",
                file_id,
                user_id,
            )
            return

        try:
            audio_bytes = tmp_path.read_bytes()
        finally:
            tmp_path.unlink(missing_ok=True)

        hub_audio = self.normalize_audio(
            msg, audio_bytes=audio_bytes, mime_type="audio/ogg"
        )

        async def _send_bp(text: str) -> None:
            await self.bot.send_message(msg.chat.id, text)

        await push_to_hub_guarded(
            inbound_bus=self._hub.inbound_audio_bus,
            platform=Platform.TELEGRAM,
            msg=hub_audio,
            circuit_registry=self._circuit_registry,
            on_drop=None,
            send_backpressure=_send_bp,
            get_msg=self._msg,
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
        chat_id = hub_msg.platform_meta.get("chat_id")

        async def _send_bp(text: str) -> None:
            if chat_id is None:
                log.error(
                    "_push_to_hub: platform_meta missing 'chat_id',"
                    " dropping backpressure ack for user_id=%s",
                    hub_msg.user_id,
                )
                return
            await self.bot.send_message(chat_id, text)

        await push_to_hub_guarded(
            inbound_bus=self._hub.inbound_bus,
            platform=Platform.TELEGRAM,
            msg=hub_msg,
            circuit_registry=self._circuit_registry,
            on_drop=on_drop,
            send_backpressure=_send_bp,
            get_msg=self._msg,
        )

    async def _on_message(self, msg) -> None:
        """Handle an incoming aiogram message: apply backpressure and put on bus."""
        if not msg.from_user or getattr(msg.from_user, "is_bot", False):
            return

        user_id = str(msg.from_user.id)
        trust = self._auth.check(user_id)
        if trust == TrustLevel.BLOCKED:
            log.info("auth_reject user=%s channel=telegram", user_id)
            return

        hub_msg = self.normalize(msg, trust_level=trust)
        log.info(
            "message_received",
            extra={
                "platform": "telegram",
                "user_id": hub_msg.user_id,
                "scope_id": hub_msg.scope_id,
                "msg_id": hub_msg.id,
            },
        )
        # IMPORTANT: Always return normally to aiogram — webhook must return
        # {"ok": True} (HTTP 200). Never raise here or Telegram will retry
        # the update indefinitely.
        await self._push_to_hub(hub_msg)

    def _render_text(self, text: str) -> list[str]:
        """Escape MarkdownV2 special characters and split into <=4096-char chunks."""
        escaped = _MARKDOWNV2_SPECIAL.sub(r"\\\1", text)
        if not escaped:
            return []
        return [
            escaped[i : i + TELEGRAM_MAX_LENGTH]
            for i in range(0, len(escaped), TELEGRAM_MAX_LENGTH)
        ]

    def _render_buttons(self, buttons: list) -> object | None:
        """Convert list[Button] to InlineKeyboardMarkup, or None if empty."""
        if not buttons:
            return None
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

        kb = [
            [
                InlineKeyboardButton(text=b.text, callback_data=b.callback_data)
                for b in buttons
            ]
        ]
        return InlineKeyboardMarkup(inline_keyboard=kb)

    async def send(
        self, original_msg: InboundMessage, outbound: OutboundMessage
    ) -> None:
        """Send a response back to Telegram via bot.send_message.

        Circuit breaker checks and recording are handled by OutboundDispatcher,
        not here. This method performs the bare send and raises on failure.
        """
        if original_msg.platform != Platform.TELEGRAM.value:
            log.error(
                "send() called with non-telegram message id=%s",
                original_msg.id,
            )
            return
        chat_id: int | None = original_msg.platform_meta.get("chat_id")
        if chat_id is None:
            raise ValueError("platform_meta missing required key 'chat_id' for send()")

        # Flatten content parts to plain text, escape and chunk
        text = outbound.to_text()
        chunks = self._render_text(text)
        keyboard = self._render_buttons(outbound.buttons)
        last_idx = len(chunks) - 1

        for i, chunk in enumerate(chunks):
            kwargs: dict = {
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": "MarkdownV2",
            }
            if i == last_idx and keyboard is not None:
                kwargs["reply_markup"] = keyboard
            sent = await self.bot.send_message(**kwargs)
            if i == last_idx:
                outbound.metadata["reply_message_id"] = sent.message_id

    async def send_streaming(  # noqa: C901 — streaming protocol: edit/chunk/finalize branches are inherently sequential
        self,
        original_msg: InboundMessage,
        chunks: AsyncIterator[str],
        outbound: OutboundMessage | None = None,
    ) -> None:
        """Stream response with edit-in-place, debounced at ~500ms.

        Circuit breaker checks and recording are handled by OutboundDispatcher,
        not here. This method performs the bare streaming send and raises on
        failure.

        When *outbound* is provided, ``outbound.metadata["reply_message_id"]``
        is set to the placeholder message ID after it is sent.
        """
        if original_msg.platform != Platform.TELEGRAM.value:
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

        parts: list[str] = []

        # Send placeholder
        _placeholder_text = self._msg("stream_placeholder", "\u2026")
        try:
            placeholder = await self.bot.send_message(
                chat_id=chat_id, text=_placeholder_text
            )
            if outbound is not None:
                outbound.metadata["reply_message_id"] = placeholder.message_id
        except Exception:
            log.exception("Failed to send placeholder — falling back to non-streaming")
            async for chunk in chunks:
                parts.append(chunk)
            fallback_content = "".join(parts) or _placeholder_text
            chunks_rendered = self._render_text(fallback_content)
            if chunks_rendered:
                fallback_msg = await self.bot.send_message(
                    chat_id=chat_id,
                    text=chunks_rendered[0],
                    parse_mode="MarkdownV2",
                )
            else:
                fallback_msg = await self.bot.send_message(
                    chat_id=chat_id, text=fallback_content
                )
            if outbound is not None:
                outbound.metadata["reply_message_id"] = fallback_msg.message_id
            return

        last_edit = time.monotonic()
        stream_error: Exception | None = None
        try:
            async for chunk in chunks:
                parts.append(chunk)
                now = time.monotonic()
                if now - last_edit >= 0.5:
                    accumulated = "".join(parts)
                    _escaped = _MARKDOWNV2_SPECIAL.sub(
                        r"\\\1", accumulated[:TELEGRAM_MAX_LENGTH]
                    )
                    await self.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=placeholder.message_id,
                        text=_escaped,
                        parse_mode="MarkdownV2",
                    )
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

        # Final edit with complete text (always runs, even after stream error)
        if accumulated:
            try:
                _escaped = _MARKDOWNV2_SPECIAL.sub(
                    r"\\\1", accumulated[:TELEGRAM_MAX_LENGTH]
                )
                await self.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=placeholder.message_id,
                    text=_escaped,
                    parse_mode="MarkdownV2",
                )
            except Exception:
                log.exception("Final edit failed")

        # Re-raise stream error so OutboundDispatcher can record CB failure
        if stream_error is not None:
            raise stream_error

    async def render_audio(self, msg: OutboundAudio, inbound: InboundMessage) -> None:
        """Send an OutboundAudio envelope as a Telegram voice note (ogg/opus).

        Uses bot.send_voice() with a BytesIO buffer — no temp file required.
        caption (if set) is attached to the voice message.
        reply_to_message_id is derived from inbound.platform_meta["message_id"]
        unless msg.reply_to_id overrides it explicitly.
        """
        if inbound.platform != Platform.TELEGRAM.value:
            log.error(
                "render_audio() called with non-telegram message id=%s",
                inbound.id,
            )
            return

        chat_id: int | None = inbound.platform_meta.get("chat_id")
        if chat_id is None:
            log.error(
                "render_audio: platform_meta missing 'chat_id' for msg id=%s",
                inbound.id,
            )
            return

        topic_id: int | None = inbound.platform_meta.get("topic_id")
        message_id: int | None = inbound.platform_meta.get("message_id")

        # Determine reply target: explicit override first, else original
        reply_to = parse_reply_to_id(msg.reply_to_id)
        if reply_to is None and message_id is not None:
            reply_to = message_id

        duration_sec: int | None = (
            msg.duration_ms // 1000 if msg.duration_ms is not None else None
        )

        audio_buf = BytesIO(msg.audio_bytes)
        audio_buf.name = "voice.ogg"

        kwargs: dict = {
            "chat_id": chat_id,
            "voice": audio_buf,
        }
        if topic_id is not None:
            kwargs["message_thread_id"] = topic_id
        if reply_to is not None:
            kwargs["reply_to_message_id"] = reply_to
        if msg.caption:
            kwargs["caption"] = msg.caption[:1024]
        if duration_sec is not None:
            kwargs["duration"] = duration_sec

        await self.bot.send_voice(**kwargs)

    async def render_attachment(
        self, msg: OutboundAttachment, inbound: InboundMessage
    ) -> None:
        """Send an OutboundAttachment envelope via the appropriate Telegram method.

        Dispatches to send_photo, send_video, or send_document based on msg.type.
        Caption, reply_to, and topic threading follow the same pattern as render_audio.
        """
        if inbound.platform != Platform.TELEGRAM.value:
            log.error(
                "render_attachment() called with non-telegram message id=%s",
                inbound.id,
            )
            return

        chat_id: int | None = inbound.platform_meta.get("chat_id")
        if chat_id is None:
            log.error(
                "render_attachment: platform_meta missing 'chat_id' for msg id=%s",
                inbound.id,
            )
            return

        topic_id: int | None = inbound.platform_meta.get("topic_id")
        message_id: int | None = inbound.platform_meta.get("message_id")

        reply_to = parse_reply_to_id(msg.reply_to_id)
        if reply_to is None and message_id is not None:
            reply_to = message_id

        buf = BytesIO(msg.data)
        # Derive safe filename: sanitize explicit name or fallback from mime
        if msg.filename:
            buf.name = sanitize_filename(
                msg.filename,
                _ATTACHMENT_EXTS,
            )
        else:
            raw_ext = msg.mime_type.split("/")[-1] if "/" in msg.mime_type else ""
            ext = raw_ext if raw_ext in _ATTACHMENT_EXTS else "bin"
            buf.name = f"attachment.{ext}"

        kwargs: dict = {"chat_id": chat_id}
        if topic_id is not None:
            kwargs["message_thread_id"] = topic_id
        if reply_to is not None:
            kwargs["reply_to_message_id"] = reply_to
        truncated = truncate_caption(msg.caption, 1024)
        if truncated:
            kwargs["caption"] = truncated

        if msg.type == "image":
            kwargs["photo"] = buf
            await self.bot.send_photo(**kwargs)
        elif msg.type == "video":
            kwargs["video"] = buf
            await self.bot.send_video(**kwargs)
        else:
            # "document" and "file" both use send_document
            kwargs["document"] = buf
            await self.bot.send_document(**kwargs)
