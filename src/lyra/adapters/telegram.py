from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import Depends, FastAPI, HTTPException, Request

if TYPE_CHECKING:
    from lyra.core.hub import Hub

from aiogram.enums import ChatAction

from lyra.core.circuit_breaker import CircuitRegistry
from lyra.core.message import (
    GENERIC_ERROR_REPLY,
    AudioContent,
    Message,
    MessageType,
    Platform,
    Response,
    TelegramContext,
    TextContent,
)
from lyra.core.messages import MessageManager

log = logging.getLogger(__name__)

TELEGRAM_MAX_LENGTH = 4096  # Telegram Bot API text message limit


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

    def _normalize(self, msg) -> Message:
        """Convert an aiogram Message (or SimpleNamespace) to a hub Message.

        Security: trust is always 'user' via Message.from_adapter().
        Never logs the bot token.
        """
        chat_type = msg.chat.type
        is_group = chat_type != "private"

        # is_mention is always False in private chats
        is_mention = False
        if is_group and msg.entities:
            for entity in msg.entities:
                if entity.type == "mention":
                    slice_text = msg.text[entity.offset : entity.offset + entity.length]
                    if slice_text == f"@{self._bot_username}":
                        is_mention = True
                        break

        platform_context = TelegramContext(
            chat_id=msg.chat.id,
            topic_id=msg.message_thread_id,
            is_group=is_group,
            message_id=getattr(msg, "message_id", None),  # stubs in tests may omit it
        )

        text = msg.text or ""
        content = TextContent(text=text)

        timestamp = msg.date
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)

        user_id = f"tg:user:{msg.from_user.id}"

        log.debug(
            "Normalizing message from user_id=%s in chat_id=%s",
            user_id,
            msg.chat.id,
        )

        return Message.from_adapter(
            platform=Platform.TELEGRAM,
            bot_id=self._bot_id,
            user_id=user_id,
            user_name=msg.from_user.full_name,
            content=content,
            type=MessageType.TEXT,
            timestamp=timestamp,
            is_mention=is_mention,
            is_from_bot=getattr(msg.from_user, "is_bot", False),
            platform_context=platform_context,
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

        platform_context = TelegramContext(
            chat_id=msg.chat.id,
            topic_id=msg.message_thread_id,
            is_group=msg.chat.type != "private",
            message_id=getattr(msg, "message_id", None),
        )
        timestamp = msg.date
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)

        hub_msg = Message.from_adapter(
            platform=Platform.TELEGRAM,
            bot_id=self._bot_id,
            user_id=f"tg:user:{msg.from_user.id}",
            user_name=msg.from_user.full_name,
            content=AudioContent(
                url=str(tmp_path),
                duration_seconds=duration_seconds,
                file_id=file_id,
            ),
            type=MessageType.AUDIO,
            timestamp=timestamp,
            is_mention=False,
            is_from_bot=False,
            platform_context=platform_context,
        )

        # Hub circuit guard — same contract as _on_message: always return normally
        # to aiogram so the webhook returns HTTP 200.
        if self._circuit_registry is not None:
            cb = self._circuit_registry.get("hub")
            if cb is not None and cb.is_open():
                log.warning(
                    '{"event": "hub_circuit_open", "platform": "telegram",'
                    ' "user_id": "%s", "dropped": true, "type": "audio"}',
                    hub_msg.user_id,
                )
                tmp_path.unlink(missing_ok=True)
                return

        try:
            self._hub.inbound_bus.put(Platform.TELEGRAM, hub_msg)
        except asyncio.QueueFull:
            tmp_path.unlink(missing_ok=True)
            text = (
                self._msg_manager.get("backpressure_ack", platform="telegram")
                if self._msg_manager
                else "Processing your request\u2026"
            )
            await self.bot.send_message(msg.chat.id, text)

    async def _on_message(self, msg) -> None:
        """Handle an incoming aiogram message: apply backpressure and put on bus."""
        if msg.from_user and getattr(msg.from_user, "is_bot", False):
            return

        hub_msg = self._normalize(msg)

        # Hub circuit guard — drop silently if hub is overloaded.
        # IMPORTANT: Always return normally to aiogram — webhook must return
        # {"ok": True} (HTTP 200). Never raise here or Telegram will retry
        # the update indefinitely.
        if self._circuit_registry is not None:
            cb = self._circuit_registry.get("hub")
            if cb is not None and cb.is_open():
                log.warning(
                    '{"event": "hub_circuit_open", "platform": "telegram",'
                    ' "user_id": "%s", "dropped": true}',
                    hub_msg.user_id,
                )
                return  # silent drop

        try:
            self._hub.inbound_bus.put(Platform.TELEGRAM, hub_msg)
        except asyncio.QueueFull:
            text = (
                self._msg_manager.get("backpressure_ack", platform="telegram")
                if self._msg_manager
                else "Processing your request\u2026"
            )
            await self.bot.send_message(
                msg.chat.id,
                text,
            )

    async def send(self, original_msg: Message, response: Response) -> None:
        """Send a response back to Telegram via bot.send_message.

        Circuit breaker checks and recording are handled by OutboundDispatcher,
        not here. This method performs the bare send and raises on failure.
        """
        if not isinstance(original_msg.platform_context, TelegramContext):
            log.error(
                "send() called with non-TelegramContext for msg_id=%s",
                original_msg.id,
            )
            return
        ctx = original_msg.platform_context

        sent = await self.bot.send_message(
            chat_id=ctx.chat_id, text=response.content
        )
        # Store for session persistence (#67) and reply-to-resume (#83).
        response.metadata["reply_message_id"] = sent.message_id

    async def send_streaming(
        self, original_msg: Message, chunks: AsyncIterator[str]
    ) -> None:
        """Stream response with edit-in-place, debounced at ~500ms.

        Circuit breaker checks and recording are handled by OutboundDispatcher,
        not here. This method performs the bare streaming send and raises on failure.

        TODO: store placeholder.message_id in response.metadata["reply_message_id"]
        once send_streaming() receives a Response argument (#67).
        """
        if not isinstance(original_msg.platform_context, TelegramContext):
            log.error(
                "send_streaming() called with non-TelegramContext for msg_id=%s",
                original_msg.id,
            )
            return
        ctx = original_msg.platform_context

        accumulated = ""

        # Send placeholder
        _placeholder_text = (
            self._msg_manager.get("stream_placeholder", platform="telegram")
            if self._msg_manager
            else "\u2026"
        )
        try:
            placeholder = await self.bot.send_message(
                chat_id=ctx.chat_id, text=_placeholder_text
            )
        except Exception:
            log.exception("Failed to send placeholder — falling back to non-streaming")
            async for chunk in chunks:
                accumulated += chunk
            fallback_content = accumulated or _placeholder_text
            await self.send(original_msg, Response(content=fallback_content))
            return

        last_edit = time.monotonic()
        stream_error: Exception | None = None
        try:
            async for chunk in chunks:
                accumulated += chunk
                now = time.monotonic()
                if now - last_edit >= 0.5:
                    await self.bot.edit_message_text(
                        chat_id=ctx.chat_id,
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
                    chat_id=ctx.chat_id,
                    message_id=placeholder.message_id,
                    text=accumulated[:TELEGRAM_MAX_LENGTH],
                )
            except Exception:
                log.exception("Final edit failed")

        # Re-raise stream error so OutboundDispatcher can record CB failure
        if stream_error is not None:
            raise stream_error
