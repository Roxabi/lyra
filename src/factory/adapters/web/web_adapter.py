"""Web smoke channel adapter — FastAPI ingress + SSE egress."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, ClassVar
from uuid import uuid4

from factory.adapters.shared._base_outbound import OutboundAdapterBase
from factory.adapters.shared._shared import TypingTaskManager
from factory.adapters.web import web_outbound
from factory.adapters.web.web_sessions import WebSessionHub
from factory.core.auth.trust import TrustLevel
from factory.core.messaging.message import (
    InboundMessage,
    OutboundAttachment,
    OutboundAudio,
    OutboundAudioChunk,
    OutboundMessage,
    WebMeta,
)
from factory.outbound.emitter import OutboundEmitter

if TYPE_CHECKING:
    from factory.adapters.shared.outbound_listener import OutboundListener
    from factory.core.messaging.bus import Bus

log = logging.getLogger(__name__)

WEB_BOT_ID = "smoke"
WEB_SCOPE_PREFIX = "agent:"


class WebAdapter(OutboundAdapterBase):
    """Browser smoke adapter — no external SDK, SSE for outbound."""

    # Web smoke has no audio egress: render_audio is a no-op and normalize_audio
    # rejects inbound audio. False keeps start_audio_consumer from binding the
    # outbound-audio KV, which the lean web ACL (ADR-079 §c) denies — an
    # ERROR-logged permission violation on every restart before this gate.
    supports_audio: ClassVar[bool] = False

    def __init__(
        self,
        *,
        bot_id: str = WEB_BOT_ID,
        inbound_bus: "Bus[InboundMessage]",
        host: str = "0.0.0.0",
        port: int = 8765,
        agent_names: list[str] | None = None,
    ) -> None:
        self._bot_id = bot_id
        self._inbound_bus = inbound_bus
        self._host = host
        self._port = port
        self._agent_names = sorted(agent_names or [])
        self._nats_client: Any = None
        self.sessions = WebSessionHub()
        self._typing = TypingTaskManager()
        self._outbound_listener: OutboundListener | None = None
        self._server_task: asyncio.Task[None] | None = None
        self._uvicorn_server: Any = None

    @property
    def agent_names(self) -> list[str]:
        return list(self._agent_names)

    def set_nats_client(self, nc: Any) -> None:
        """Wire NATS client for dashboard BFF hub RPC."""
        self._nats_client = nc

    @property
    def ready(self) -> bool:
        """True once the outbound listener is wired (NATS correlation ready)."""
        return self._outbound_listener is not None

    def normalize(
        self,
        raw: dict[str, Any],
        *,
        trust_level: TrustLevel = TrustLevel.TRUSTED,
        is_admin: bool = False,
    ) -> InboundMessage:
        del is_admin
        agent = str(raw.get("agent", "")).strip()
        text = str(raw.get("text", "")).strip()
        session_id = str(raw.get("session_id") or uuid4().hex)
        harness = str(raw.get("harness") or "").strip() or None
        model = str(raw.get("model") or "").strip() or None
        if not agent or not text:
            raise ValueError("agent and text are required")
        if agent not in self._agent_names:
            raise ValueError(f"unknown agent: {agent!r}")
        scope_id = f"{WEB_SCOPE_PREFIX}{agent}"
        return InboundMessage(
            id=uuid4().hex,
            platform="web",
            bot_id=self._bot_id,
            scope_id=scope_id,
            user_id="smoke",
            user_name="Smoke",
            is_mention=True,
            text=text,
            text_raw=text,
            timestamp=datetime.now(timezone.utc),
            platform_meta=WebMeta(
                session_id=session_id,
                harness=harness,
                model=model,
            ),
            trust_level=trust_level,
        )

    def normalize_audio(
        self,
        raw: Any,
        audio_bytes: bytes,
        mime_type: str,
        *,
        trust_level: TrustLevel,
        pending: Any = None,
    ) -> InboundMessage:
        del raw, audio_bytes, mime_type, trust_level, pending
        raise NotImplementedError("Web smoke adapter does not accept audio inbound")

    async def send(
        self,
        original_msg: InboundMessage,
        outbound: OutboundMessage,
    ) -> None:
        await web_outbound.send(self, original_msg, outbound)

    def _make_emitter(
        self,
        original_msg: InboundMessage,
        outbound: OutboundMessage | None,
    ) -> OutboundEmitter:
        return web_outbound._make_emitter(self, original_msg, outbound)

    def _start_typing(self, scope_id: int) -> None:
        del scope_id

    def _cancel_typing(self, scope_id: int) -> None:
        del scope_id

    async def render_audio(self, msg: OutboundAudio, inbound: InboundMessage) -> None:
        del msg, inbound

    async def render_audio_stream(
        self,
        chunks: AsyncIterator[OutboundAudioChunk],
        inbound: InboundMessage,
    ) -> None:
        del chunks, inbound

    async def render_attachment(
        self, msg: OutboundAttachment, inbound: InboundMessage
    ) -> None:
        del msg, inbound

    async def astart(self) -> None:
        if self._outbound_listener is not None:
            await self._outbound_listener.start()
        from factory.adapters.web.web_server import create_app, run_uvicorn
        from factory.infrastructure.stores.identity.control_plane_open import (
            open_control_plane_store,
        )

        self._control_plane = await open_control_plane_store()
        app = create_app(self, control_plane=self._control_plane)
        self._server_task = asyncio.create_task(
            run_uvicorn(app, host=self._host, port=self._port, server_holder=self),
            name=f"web:{self._bot_id}",
        )
        log.info(
            "Web smoke adapter listening on http://%s:%d",
            self._host,
            self._port,
        )

    async def close(self) -> None:
        if self._uvicorn_server is not None:
            self._uvicorn_server.should_exit = True
        if self._server_task is not None:
            self._server_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._server_task
            self._server_task = None
        cp = getattr(self, "_control_plane", None)
        if cp is not None:
            with contextlib.suppress(Exception):
                await cp.close()
            self._control_plane = None
        if self._outbound_listener is not None:
            await self._outbound_listener.stop()
