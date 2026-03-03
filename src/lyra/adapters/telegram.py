from __future__ import annotations

import logging
from datetime import timezone
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request

from lyra.core.message import (
    Message,
    MessageType,
    Platform,
    Response,
    TelegramContext,
    TextContent,
)

log = logging.getLogger(__name__)


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
        hub,
        bot_username: str = "lyra_bot",
        webhook_secret: str = "",
    ) -> None:
        self._bot_id = bot_id
        self._token = token  # kept private — never logged
        self._webhook_secret = webhook_secret
        self._bot_username = bot_username
        self._hub = hub

        # bot is a public attribute so tests can replace it with AsyncMock.
        # Deferred lazily so tests can assign adapter.bot = AsyncMock() after
        # construction without triggering aiogram token validation at test time.
        self._bot: Any = None
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

    def _register_routes(self) -> None:
        verifier = _make_verifier(self._webhook_secret)

        @self.app.post(
            "/webhooks/telegram/{bot_id}",
            dependencies=[Depends(verifier)],
        )
        async def handle_update(bot_id: str, request: Request) -> dict:
            await request.json()
            log.debug("Received update for bot_id=%s", bot_id)
            return {"ok": True}

    def _normalize(self, msg, *, bot_username: str) -> Message:
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
                    slice_text = msg.text[entity.offset: entity.offset + entity.length]
                    if slice_text == f"@{bot_username}":
                        is_mention = True
                        break

        platform_context = TelegramContext(
            chat_id=msg.chat.id,
            topic_id=msg.message_thread_id,
            is_group=is_group,
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

        hub_msg = self._normalize(msg, bot_username=self._bot_username)

        if self._hub.bus.full():
            await self.bot.send_message(
                msg.chat.id,
                "Processing your request\u2026",
            )

        await self._hub.bus.put(hub_msg)

    async def send(self, original_msg: Message, response: Response) -> None:
        """Send a response back to Telegram via bot.send_message."""
        ctx: TelegramContext = original_msg.platform_context  # type: ignore[assignment]
        await self.bot.send_message(chat_id=ctx.chat_id, text=response.content)
