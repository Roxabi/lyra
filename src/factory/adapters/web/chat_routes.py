"""Chat adapter axis routes — POST /api/chat, GET /api/stream, GET /api/agents."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from factory.adapters.shared.inbound import (
    build_web_inbound_ctx,
    get_inbound_pipeline_kit,
    get_or_create_parser,
    run_inbound_guarded,
)
from factory.adapters.web.e2e_stub import e2e_enabled, publish_e2e_agui_reply
from factory.adapters.web.web_agui import StreamFormat, is_stream_terminal, run_error
from factory.core.auth.control_plane import ControlPlanePrincipal
from factory.dashboard.auth import require_principal
from factory.inbound.wire_parser_web import WebWireParser
from roxabi_contracts.dashboard import ChatRequest, ChatResponse

if TYPE_CHECKING:
    from factory.adapters.web.web_adapter import WebAdapter
    from factory.dashboard.stream_tokens import StreamTokenRegistry


async def _no_attachment_ingest(_exc: object) -> None:
    return None


def build_chat_router(  # noqa: C901
    adapter: WebAdapter,
    tokens: StreamTokenRegistry,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/agents")
    async def list_agents(
        _principal: ControlPlanePrincipal = Depends(require_principal),
    ) -> dict[str, list[str]]:
        del _principal
        return {"agents": adapter.agent_names}

    @router.post("/api/chat", response_model=ChatResponse)
    async def post_chat(
        req: ChatRequest,
        format: StreamFormat = Query(default="legacy"),
        principal: ControlPlanePrincipal = Depends(require_principal),
    ) -> ChatResponse:
        session_id = req.session_id or uuid4().hex
        raw = {
            "agent": req.agent,
            "text": req.text,
            "session_id": session_id,
            "harness": req.harness,
            "model": req.model,
            "user_id": principal.user_id,
            "user_name": principal.user_id,
        }
        try:
            adapter.normalize(raw)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not adapter.ready:
            raise HTTPException(status_code=503, detail="adapter not ready")
        adapter.sessions.set_stream_format(session_id, format)

        kit = get_inbound_pipeline_kit()
        parser = get_or_create_parser(kit.parser_cache, adapter, WebWireParser)

        async def _web_backpressure(text: str) -> None:
            if format == "agui":
                await adapter.sessions.publish(session_id, run_error(message=text))
            else:
                await adapter.sessions.publish(
                    session_id, {"type": "error", "message": text}
                )

        await run_inbound_guarded(
            pipeline=kit.pipeline,
            raw_message=raw,
            inbound_ctx=build_web_inbound_ctx(adapter),
            parser=parser,
            log_context=f"web session_id={session_id}",
            on_attachment_ingest_error=_no_attachment_ingest,
            send_backpressure=_web_backpressure,
            on_drop=None,
        )
        if e2e_enabled() and format == "agui":
            asyncio.create_task(
                publish_e2e_agui_reply(adapter.sessions, session_id, req.text)
            )
        stream_token = tokens.mint(session_id)
        return ChatResponse(session_id=session_id, stream_token=stream_token)

    @router.get("/api/stream/{session_id}")
    async def stream(
        session_id: str,
        token: str | None = Query(default=None),
        format: StreamFormat = Query(default="legacy"),
    ) -> StreamingResponse:
        if not tokens.verify(session_id, token):
            raise HTTPException(status_code=403, detail="invalid stream token")
        session = adapter.sessions.get_or_create(session_id)
        if session.stream_format != format:
            raise HTTPException(
                status_code=403,
                detail="stream format mismatch",
            )

        async def event_gen() -> AsyncIterator[str]:
            try:
                while not session.closed:
                    try:
                        item = await asyncio.wait_for(
                            session.queue.get(), timeout=120.0
                        )
                    except TimeoutError:
                        if format == "agui":
                            yield ": ping\n\n"
                        else:
                            yield "data: " + json.dumps({"type": "ping"}) + "\n\n"
                        continue
                    yield "data: " + json.dumps(item) + "\n\n"
                    if is_stream_terminal(item, format):
                        break
            finally:
                adapter.sessions.close(session_id)
                tokens.revoke(session_id)

        return StreamingResponse(event_gen(), media_type="text/event-stream")

    return router
