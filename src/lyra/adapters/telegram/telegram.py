"""Telegram adapter facade — delegates to telegram_* submodules."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator, Coroutine
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import Depends, FastAPI, HTTPException, Request

if TYPE_CHECKING:
    from lyra.adapters.shared.outbound_listener import OutboundListener
    from lyra.core.messaging.bus import Bus
    from lyra.core.ports.blobstore import BlobStorePort
    from lyra.core.stores import TurnStoreProtocol
    from lyra.inbound.attachment_ingest import IngestCtx
    from lyra.outbound.emitter import OutboundEmitter

from lyra.adapters.telegram import telegram_audio  # noqa: I001 — DEBT:lint-residual
from lyra.adapters.shared._base_outbound import OutboundAdapterBase
from lyra.adapters.shared._shared import TypingTaskManager, resolve_msg
from lyra.typing import make_typing_factory
from lyra.adapters.telegram.telegram_guard import _make_verifier
from lyra.adapters.telegram.telegram_inbound import handle_message, handle_voice_message
from lyra.adapters.telegram.telegram_normalize import (
    normalize as _normalize_impl,
    normalize_audio as _normalize_audio_impl,
)
from lyra.adapters.telegram.telegram_outbound import (
    _typing_loop as _typing_loop,  # noqa: F401 — DEBT:re-export-init
    _typing_worker,
    send as _send_impl,
)
from lyra.core.circuit_breaker import CircuitRegistry
from lyra.core.auth.guard import BlockedGuard, GuardChain
from lyra.core.auth.trust import TrustLevel
from lyra.core.config import TelegramConfig as TelegramConfig, load_telegram_config
from lyra.core.messaging.message import (
    InboundMessage,
    OutboundAttachment,
    OutboundAudio,
    OutboundAudioChunk,
    OutboundMessage,
)
from lyra.core.messaging.messages import MessageManager

log = logging.getLogger(__name__)


# ── Typing plane (#1376) — module-level resolver for AC8 ─────────────────
from lyra.transport.work_scope import WorkScope  # noqa: E402


def _telegram_scope_resolver(scope: WorkScope) -> int:
    """Resolve WorkScope → Telegram chat_id (may be negative for groups)."""
    return scope.scope_id


load_config = load_telegram_config  # backward-compat alias (ADR-059 V6)


class TelegramAdapter(OutboundAdapterBase):
    """Telegram adapter — aiogram v3 webhook. Never logs the bot token."""

    def __init__(  # noqa: PLR0913 — DEBT:wiring-bootstrap-deps — DI constructor
        self,
        bot_id: str,
        token: str,
        inbound_bus: "Bus[InboundMessage]",
        webhook_secret: str = "",
        circuit_registry: CircuitRegistry | None = None,
        msg_manager: MessageManager | None = None,
        turn_store: "TurnStoreProtocol | None" = None,
        blob_store: "BlobStorePort | None" = None,
    ) -> None:
        super().__init__()  # no-op today, future-proofs cooperative chain
        self._bot_id = bot_id
        self._token = token
        self._webhook_secret = webhook_secret
        if not self._webhook_secret:
            log.warning(
                "webhook_secret is empty — all webhook requests will be rejected"
            )
        self._bot_username: str | None = None
        self._inbound_bus = inbound_bus
        self._circuit_registry = circuit_registry
        self._msg_manager = msg_manager
        self._guard_chain: GuardChain = GuardChain([BlockedGuard()])
        self._turn_store: "TurnStoreProtocol | None" = turn_store
        self._blob_store: "BlobStorePort | None" = blob_store
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
        self._factory_builder = make_typing_factory(self._typing_worker_bound)
        self._dp: Any = None
        from aiogram import Dispatcher, F

        self._dp = Dispatcher()
        self._dp.message.register(
            self._on_voice_message, F.voice | F.audio | F.video_note
        )
        self._dp.message.register(self._on_message)

        self.app = FastAPI()
        self._register_routes()
        self._outbound_listener: "OutboundListener | None" = None
        # Post-construction injection (same pattern as _outbound_listener).
        # Carries the BlobStorePort for AttachmentIngestStage; None = no-op.
        self._ingest_ctx: "IngestCtx | None" = None

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
        me = await self.bot.get_me()
        self._bot_username = me.username
        log.info(
            "resolve_identity: bot_id=%s username=@%s", self._bot_id, self._bot_username
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

    def _typing_worker_bound(self, chat_id: int) -> Coroutine[Any, Any, None]:
        """Bound worker that reads ``self.bot`` lazily (tests replace via setter)."""
        return _typing_worker(self.bot, chat_id)

    def _start_typing(self, scope_id: int) -> None:
        self._typing.start(scope_id, self._factory_builder(scope_id))

    def _cancel_typing(self, scope_id: int) -> None:
        self._typing.cancel(scope_id)

    async def astart(self) -> None:
        if self._outbound_listener is not None:
            await self._outbound_listener.start()

    async def close(self) -> None:
        await self._typing.cancel_all()
        if self._outbound_listener is not None:
            await self._outbound_listener.stop()

    # --- Thin delegates to submodules ---

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
        self,
        raw: Any,
        audio_bytes: bytes,
        mime_type: str,
        *,
        trust_level: TrustLevel,
        pending: Any = None,
    ) -> InboundMessage:
        return _normalize_audio_impl(
            self,
            raw,
            audio_bytes,
            mime_type,
            trust_level=trust_level,
            pending=pending,
        )

    async def send(
        self, original_msg: InboundMessage, outbound: OutboundMessage
    ) -> None:
        await _send_impl(self, original_msg, outbound)

    def _make_emitter(
        self,
        original_msg: InboundMessage,
        outbound: OutboundMessage | None,
    ) -> "OutboundEmitter":
        """Construct an OutboundEmitter composed from stage objects (#1279, S7)."""
        from lyra.adapters.shared._emitter import _make_emitter as _shared
        from lyra.adapters.telegram.telegram_formatter import TelegramFormatter
        from lyra.adapters.telegram.telegram_formatting import _validate_inbound
        from lyra.adapters.telegram.telegram_outbound import TelegramTypingIndicator
        from lyra.core.messaging.message import TelegramMeta

        _pm = original_msg.platform_meta
        return _shared(
            self,
            original_msg,
            outbound,
            validate=_validate_inbound,
            bad_msg="invalid inbound message",
            formatter_cls=TelegramFormatter,
            formatter_kwargs_fn=lambda m: {
                "chat_id": m[0],
                "reply_to": _pm.message_id if isinstance(_pm, TelegramMeta) else None,
            },
            typing_cls=TelegramTypingIndicator,
            scope_id_fn=lambda m: m[0],
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
