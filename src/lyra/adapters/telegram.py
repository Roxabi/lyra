from __future__ import annotations

import logging
import os
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timezone
from typing import TYPE_CHECKING, Any

from fastapi import Depends, FastAPI, HTTPException, Request

if TYPE_CHECKING:
    from lyra.core.hub import Hub

from lyra.core.message import (
    GENERIC_ERROR_REPLY,
    Message,
    MessageType,
    Platform,
    Response,
    TelegramContext,
    TextContent,
)

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

        # bot is a public attribute so tests can replace it with AsyncMock.
        # Deferred lazily so tests can assign adapter.bot = AsyncMock() after
        # construction without triggering aiogram token validation at test time.
        self._bot: Any = None
        self._dp: Any = None

        from aiogram import Dispatcher

        self._dp = Dispatcher()
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
            message_id=getattr(msg, "message_id", None),
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

    async def _on_message(self, msg) -> None:
        """Handle an incoming aiogram message: apply backpressure and put on bus."""
        if msg.from_user and getattr(msg.from_user, "is_bot", False):
            return

        hub_msg = self._normalize(msg)

        if self._hub.bus.full():
            await self.bot.send_message(
                msg.chat.id,
                "Processing your request\u2026",
            )

        await self._hub.bus.put(hub_msg)

    async def send(self, original_msg: Message, response: Response) -> None:
        """Send a response back to Telegram via bot.send_message."""
        if not isinstance(original_msg.platform_context, TelegramContext):
            log.error(
                "send() called with non-TelegramContext for msg_id=%s", original_msg.id
            )
            return
        ctx = original_msg.platform_context
        sent = await self.bot.send_message(chat_id=ctx.chat_id, text=response.content)
        response.metadata["reply_message_id"] = sent.message_id

    async def send_streaming(
        self, original_msg: Message, chunks: AsyncIterator[str]
    ) -> None:
        """Stream response with edit-in-place, debounced at ~500ms."""
        if not isinstance(original_msg.platform_context, TelegramContext):
            log.error(
                "send_streaming() called with non-TelegramContext for msg_id=%s",
                original_msg.id,
            )
            return
        ctx = original_msg.platform_context
        accumulated = ""

        # Send placeholder
        try:
            placeholder = await self.bot.send_message(
                chat_id=ctx.chat_id, text="\u2026"
            )
        except Exception:
            log.exception("Failed to send placeholder — falling back to non-streaming")
            async for chunk in chunks:
                accumulated += chunk
            await self.send(original_msg, Response(content=accumulated or "\u2026"))
            return

        last_edit = time.monotonic()
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
        except Exception:
            log.exception("Stream interrupted")
            if accumulated:
                accumulated += " [response interrupted]"
            else:
                accumulated = GENERIC_ERROR_REPLY

        # Final edit with complete text
        if accumulated:
            try:
                await self.bot.edit_message_text(
                    chat_id=ctx.chat_id,
                    message_id=placeholder.message_id,
                    text=accumulated[:TELEGRAM_MAX_LENGTH],
                )
            except Exception:
                log.exception("Final edit failed")
