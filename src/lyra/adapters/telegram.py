"""Telegram adapter facade — delegates to telegram_* submodules."""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import Depends, FastAPI, HTTPException, Request

if TYPE_CHECKING:
    from lyra.core.hub import Hub

from lyra.adapters import telegram_audio  # noqa: I001
from lyra.adapters._shared import TypingTaskManager, resolve_msg
from lyra.adapters.telegram_formatting import (
    _render_buttons as _render_buttons_impl,
    _render_text as _render_text_impl,
)
from lyra.adapters.telegram_inbound import handle_message, handle_voice_message
from lyra.adapters.telegram_normalize import (
    normalize as _normalize_impl,
    normalize_audio as _normalize_audio_impl,
)
from lyra.adapters.telegram_outbound import (
    _typing_loop,  # noqa: F401 — re-exported for tests
    _typing_worker,
    send as _send_impl,
    send_streaming as _send_streaming_impl,
)
from lyra.core.auth import _ALLOW_ALL, _DENY_ALL, AuthMiddleware  # noqa: F401 — re-exported for tests and external callers
from lyra.core.circuit_breaker import CircuitRegistry
from lyra.core.trust import TrustLevel
from lyra.core.message import (
    InboundAudio,
    InboundMessage,
    OutboundAttachment,
    OutboundAudio,
    OutboundAudioChunk,
    OutboundMessage,
)
from lyra.core.messages import MessageManager

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TelegramConfig:
    token: str
    webhook_secret: str
    bot_username: str


def load_config() -> TelegramConfig:
    """Load Telegram configuration from environment variables."""
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token:
        raise SystemExit("Missing required env var: TELEGRAM_TOKEN")
    secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET")
    if not secret:
        raise SystemExit("Missing required env var: TELEGRAM_WEBHOOK_SECRET")
    bot_username = os.environ.get("TELEGRAM_BOT_USERNAME", "lyra_bot")
    return TelegramConfig(token=token, webhook_secret=secret, bot_username=bot_username)


def _make_verifier(secret: str):
    """Return a FastAPI dependency that validates the Telegram webhook secret."""

    async def verify(request: Request) -> None:
        incoming = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not secret or not hmac.compare_digest(incoming, secret):
            raise HTTPException(status_code=401, detail="Unauthorized")

    return verify


class TelegramAdapter:
    """Telegram channel adapter — aiogram v3 webhook style.

    Never logs the bot token. Webhook route validates secret token header.
    """

    def __init__(  # noqa: PLR0913 — DI constructor
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
        self._token = token
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
        _raw_tmp = os.environ.get("LYRA_AUDIO_TMP") or None
        if _raw_tmp is not None:
            _tmp_path = Path(_raw_tmp)
            if not _tmp_path.is_dir():
                raise RuntimeError(
                    f"LYRA_AUDIO_TMP={_raw_tmp!r} does not exist or is not a directory"
                )
            if not os.access(_raw_tmp, os.W_OK):
                raise RuntimeError(
                    f"LYRA_AUDIO_TMP={_raw_tmp!r} is not writable by the current"
                    " process"
                )
        self._audio_tmp_dir: str | None = _raw_tmp
        self._max_audio_bytes: int = int(
            os.environ.get("LYRA_MAX_AUDIO_BYTES", 5 * 1024 * 1024)
        )
        self._typing = TypingTaskManager()
        self._bot: Any = None
        self._dp: Any = None

        from aiogram import Dispatcher, F

        self._dp = Dispatcher()
        self._dp.message.register(
            self._on_voice_message, F.voice | F.audio | F.video_note
        )
        self._dp.message.register(self._on_message)

        self.app = FastAPI()
        self._register_routes()

    @property
    def bot(self) -> Any:
        """Lazy aiogram Bot — tests replace via ``adapter.bot = AsyncMock()``."""
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
                    name: {"state": s.state.value, "retry_after": s.retry_after}
                    for name, s in all_status.items()
                },
                "timestamp": ts,
            }

    def _msg(self, key: str, fallback: str) -> str:
        return resolve_msg(
            self._msg_manager, key, platform="telegram", fallback=fallback
        )

    @property
    def _typing_tasks(self) -> dict[int, asyncio.Task]:
        """Expose the internal task dict — used by tests and outbound submodules."""
        return self._typing._tasks

    def _start_typing(self, chat_id: int) -> None:
        self._typing.start(chat_id, lambda: _typing_worker(self.bot, chat_id))

    def _cancel_typing(self, chat_id: int) -> None:
        self._typing.cancel(chat_id)

    async def close(self) -> None:
        await self._typing.cancel_all()

    # --- Thin delegates to submodules ---

    def _render_text(self, text: str) -> list[str]:
        return _render_text_impl(text)

    def _render_buttons(self, buttons: list) -> object | None:
        return _render_buttons_impl(buttons)

    async def _on_message(self, msg: Any) -> None:
        await handle_message(self, msg)

    async def _on_voice_message(self, msg: Any) -> None:
        await handle_voice_message(self, msg)

    def normalize(
        self, raw: Any, *, trust_level: TrustLevel = TrustLevel.TRUSTED
    ) -> InboundMessage:
        return _normalize_impl(self, raw, trust_level=trust_level)

    def normalize_audio(
        self, raw: Any, audio_bytes: bytes, mime_type: str, *, trust_level: TrustLevel
    ) -> InboundAudio:
        return _normalize_audio_impl(
            self, raw, audio_bytes, mime_type, trust_level=trust_level
        )

    async def send(
        self, original_msg: InboundMessage, outbound: OutboundMessage
    ) -> None:
        await _send_impl(self, original_msg, outbound)

    async def send_streaming(
        self,
        original_msg: InboundMessage,
        chunks: AsyncIterator[str],
        outbound: OutboundMessage | None = None,
    ) -> None:
        await _send_streaming_impl(self, original_msg, chunks, outbound)

    async def render_audio(self, msg: OutboundAudio, inbound: InboundMessage) -> None:
        await telegram_audio.render_audio(self, msg, inbound)

    async def render_attachment(
        self, msg: OutboundAttachment, inbound: InboundMessage
    ) -> None:
        await telegram_audio.render_attachment(self, msg, inbound)

    async def render_audio_stream(
        self, chunks: AsyncIterator[OutboundAudioChunk], inbound: InboundMessage
    ) -> None:
        await telegram_audio.render_audio_stream(self, chunks, inbound)

    async def render_voice_stream(
        self, chunks: AsyncIterator[OutboundAudioChunk], inbound: InboundMessage
    ) -> None:
        await telegram_audio.render_voice_stream(chunks, inbound)
