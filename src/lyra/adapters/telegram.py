"""Telegram adapter facade — delegates to telegram_* submodules."""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from lyra.adapters._shared_streaming import PlatformCallbacks
    from lyra.adapters.nats_outbound_listener import NatsOutboundListener
    from lyra.core.bus import Bus

from lyra.adapters import telegram_audio  # noqa: I001
from lyra.adapters._base_outbound import OutboundAdapterBase
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
    _typing_loop as _typing_loop,  # noqa: F401
    _typing_worker,
    send as _send_impl,
)
from lyra.core.auth import (  # noqa: F401
    _ALLOW_ALL as _ALLOW_ALL,
    _DENY_ALL as _DENY_ALL,
    AuthMiddleware as AuthMiddleware,
)

from lyra.core.authenticator import Authenticator
from lyra.core.circuit_breaker import CircuitRegistry
from lyra.core.guard import BlockedGuard, GuardChain
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


class TelegramConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    token: str
    webhook_secret: str


def load_config() -> TelegramConfig:
    """Load Telegram configuration from environment variables."""
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token:
        raise SystemExit("Missing required env var: TELEGRAM_TOKEN")
    secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET")
    if not secret:
        raise SystemExit("Missing required env var: TELEGRAM_WEBHOOK_SECRET")
    return TelegramConfig(token=token, webhook_secret=secret)


def _make_verifier(secret: str):
    """Return a FastAPI dependency that validates the Telegram webhook secret."""
    async def verify(request: Request) -> None:
        incoming = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not secret or not hmac.compare_digest(incoming, secret):
            raise HTTPException(status_code=401, detail="Unauthorized")
    return verify


class TelegramAdapter(OutboundAdapterBase):
    """Telegram adapter — aiogram v3 webhook. Never logs the bot token."""

    def __init__(  # noqa: PLR0913 — DI constructor
        self,
        bot_id: str,
        token: str,
        inbound_bus: "Bus[InboundMessage]",
        inbound_audio_bus: "Bus[InboundAudio]",
        webhook_secret: str = "",
        circuit_registry: CircuitRegistry | None = None,
        msg_manager: MessageManager | None = None,
        auth: Authenticator = _DENY_ALL,
    ) -> None:
        super().__init__()  # no-op today, future-proofs cooperative chain
        if auth is not _DENY_ALL:
            import warnings

            warnings.warn(
                "TelegramAdapter(auth=...) is deprecated after C3 — "
                "use hub.register_authenticator() instead.",
                DeprecationWarning,
                stacklevel=2,
            )
        self._bot_id = bot_id
        self._token = token
        self._webhook_secret = webhook_secret
        if not self._webhook_secret:
            log.warning(
                "webhook_secret is empty — all webhook requests will be rejected"
            )
        self._bot_username: str | None = None
        self._inbound_bus = inbound_bus
        self._inbound_audio_bus = inbound_audio_bus
        self._circuit_registry = circuit_registry
        self._msg_manager = msg_manager
        self._auth: Authenticator = auth
        self._guard_chain: GuardChain = GuardChain([BlockedGuard()])
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
        self._outbound_listener: "NatsOutboundListener | None" = None

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

    async def resolve_identity(self) -> None:
        """Discover the bot's username via getMe — call once after startup."""
        me = await self.bot.get_me()
        self._bot_username = me.username
        log.info(
            "resolve_identity: bot_id=%s username=@%s",
            self._bot_id,
            self._bot_username,
        )

    @property
    def dp(self) -> Any:
        return self._dp

    def _register_routes(self) -> None:
        verifier = _make_verifier(self._webhook_secret)

        @self.app.post(
            "/webhooks/telegram/{bot_id}",
            dependencies=[Depends(verifier)],
        )
        async def handle_update(bot_id: str, request: Request) -> dict[str, Any]:
            if bot_id != self._bot_id:
                raise HTTPException(status_code=404, detail="Not Found")
            from aiogram.types import Update

            body = await request.json()
            update = Update.model_validate(body)
            await self._dp.feed_update(self.bot, update)
            log.debug("Dispatched update for bot_id=%s", bot_id)
            return {"ok": True}

        @self.app.get("/status", dependencies=[Depends(verifier)])
        async def get_status() -> dict[str, Any]:
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
    def _typing_tasks(self) -> dict[int, asyncio.Task[None]]:
        """Expose the internal task dict — used by tests and outbound submodules."""
        return self._typing._tasks

    def _start_typing(self, chat_id: int) -> None:
        self._typing.start(chat_id, lambda: _typing_worker(self.bot, chat_id))

    def _cancel_typing(self, chat_id: int) -> None:
        self._typing.cancel(chat_id)

    async def astart(self) -> None:
        if self._outbound_listener is not None:
            await self._outbound_listener.start()

    async def close(self) -> None:
        await self._typing.cancel_all()
        if self._outbound_listener is not None:
            await self._outbound_listener.stop()

    # --- Thin delegates to submodules ---

    def _render_text(self, text: str) -> list[str]:
        return _render_text_impl(text)

    def _render_buttons(self, buttons: list[Any]) -> object | None:
        return _render_buttons_impl(buttons)

    async def _on_message(self, msg: Any) -> None:
        await handle_message(self, msg)

    async def _on_voice_message(self, msg: Any) -> None:
        await handle_voice_message(self, msg)

    def normalize(
        self,
        raw: Any,
        *,
        trust_level: TrustLevel = TrustLevel.TRUSTED,
        is_admin: bool = False,
    ) -> InboundMessage:
        return _normalize_impl(self, raw, trust_level=trust_level, is_admin=is_admin)

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

    def _make_streaming_callbacks(  # noqa: C901 — streaming callbacks: one closure per platform op
        self,
        original_msg: InboundMessage,
        outbound: OutboundMessage | None,
    ) -> "PlatformCallbacks":
        from lyra.adapters._shared_streaming import PlatformCallbacks
        from lyra.adapters.telegram_formatting import _validate_inbound

        meta = _validate_inbound(original_msg, "send_streaming")
        if meta is None:
            # Return no-op callbacks that immediately signal failure;
            # send_placeholder raises, triggering the fallback path.
            async def _noop_placeholder():
                raise ValueError("invalid inbound message")

            async def _noop_fallback(text: str) -> None:
                return None

            return PlatformCallbacks(
                send_placeholder=_noop_placeholder,
                edit_placeholder_text=lambda ph, text: asyncio.sleep(0),
                edit_placeholder_tool=lambda ph, ev, header: asyncio.sleep(0),
                send_message=_noop_fallback,
                send_fallback=_noop_fallback,
                chunk_text=lambda text: [text],
                start_typing=lambda: None,
                cancel_typing=lambda: None,
            )

        chat_id, _, _ = meta
        reply_to: int | None = original_msg.platform_meta.get("message_id")
        _placeholder_text = self._msg("stream_placeholder", "\u2026")

        async def _send_placeholder():
            msg = await self.bot.send_message(
                chat_id=chat_id,
                text=_placeholder_text,
                **({"reply_to_message_id": reply_to} if reply_to is not None else {}),
            )
            return msg, msg.message_id

        async def _edit_placeholder_text(ph: object, text: str) -> None:
            rendered = _render_text_impl(text)
            if rendered:
                try:
                    await self.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=ph.message_id,
                        text=rendered[0],
                        parse_mode="MarkdownV2",
                    )
                except Exception as exc:
                    log.debug("Placeholder text edit skipped: %s", exc)

        async def _edit_placeholder_tool(  # noqa: E501
            ph: object, event: object, header: str
        ) -> None:
            from lyra.adapters.telegram_outbound import _format_tool_summary
            summary = _format_tool_summary(event)
            rendered = _render_text_impl(summary)
            if rendered:
                try:
                    await self.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=ph.message_id,
                        text=rendered[0],
                        parse_mode="MarkdownV2",
                    )
                except Exception as exc:
                    log.debug("Tool summary edit skipped: %s", exc)

        async def _send_message(text: str) -> "int | None":
            rendered = _render_text_impl(text)
            last = None
            for chunk in rendered:
                try:
                    last = await self.bot.send_message(
                        chat_id=chat_id,
                        text=chunk,
                        parse_mode="MarkdownV2",
                    )
                except Exception:
                    log.exception("Failed to send final text chunk")
            return last.message_id if last else None

        async def _send_fallback(text: str) -> "int | None":
            """NOT self.send() — needs MarkdownV2 escaping."""
            rendered = _render_text_impl(text) if text else []
            if not rendered:
                rendered = [text or _placeholder_text]
            last = None
            for chunk in rendered:
                last = await self.bot.send_message(
                    chat_id=chat_id,
                    text=chunk,
                    parse_mode="MarkdownV2",
                )
            return last.message_id if last else None

        scope_id = chat_id

        return PlatformCallbacks(
            send_placeholder=_send_placeholder,
            edit_placeholder_text=_edit_placeholder_text,
            edit_placeholder_tool=_edit_placeholder_tool,
            send_message=_send_message,
            send_fallback=_send_fallback,
            chunk_text=lambda text: _render_text_impl(text) or [text],
            start_typing=lambda: self._start_typing(scope_id),
            cancel_typing=lambda: self._cancel_typing(scope_id),
        )

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
