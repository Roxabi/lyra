"""FastAPI surface for the web smoke adapter."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from factory.adapters.shared.inbound import (
    build_web_inbound_ctx,
    get_inbound_pipeline_kit,
    get_or_create_parser,
    run_inbound_guarded,
)
from factory.inbound.wire_parser_web import WebWireParser

if TYPE_CHECKING:
    from factory.adapters.web.web_adapter import WebAdapter
    from factory.inbound.attachment_ingest import AttachmentIngestError


async def _no_attachment_ingest(_exc: "AttachmentIngestError") -> None:
    """Web smoke accepts no attachments — the ingest stage never errors."""
    return None


_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Factory Web Smoke</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 2rem; max-width: 48rem; }
    #log {
      border: 1px solid #ccc; min-height: 12rem; padding: 1rem; white-space: pre-wrap;
    }
    .row { display: flex; gap: 0.5rem; margin-top: 1rem; }
    select, input, button { font-size: 1rem; padding: 0.4rem; }
    input { flex: 1; }
  </style>
</head>
<body>
  <h1>Factory Web Smoke</h1>
  <label>Agent <select id="agent"></select></label>
  <div class="row">
    <input id="text" placeholder="Message" />
    <button id="send">Send</button>
  </div>
  <pre id="log"></pre>
  <script>
    const log = document.getElementById('log');
    const agentSel = document.getElementById('agent');
    const textEl = document.getElementById('text');
    let sessionId = null;
    let source = null;

    async function loadAgents() {
      const res = await fetch('/api/agents');
      const data = await res.json();
      agentSel.innerHTML = '';
      for (const name of data.agents) {
        const opt = document.createElement('option');
        opt.value = name; opt.textContent = name;
        agentSel.appendChild(opt);
      }
    }

    function append(t) { log.textContent += t; }

    function connectStream(sid) {
      if (source) source.close();
      source = new EventSource('/api/stream/' + sid);
      source.onmessage = (ev) => {
        const msg = JSON.parse(ev.data);
        if (msg.type === 'delta') append(msg.text || '');
        if (msg.type === 'done') append('\\n---\\n');
        if (msg.type === 'error') append('\\n[error] ' + msg.message + '\\n');
      };
    }

    document.getElementById('send').onclick = async () => {
      const body = { agent: agentSel.value, text: textEl.value, session_id: sessionId };
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) { append('[send failed]\\n'); return; }
      const data = await res.json();
      sessionId = data.session_id;
      connectStream(sessionId);
      textEl.value = '';
      append('> ' + body.text + '\\n');
    };

    loadAgents();
  </script>
</body>
</html>"""


class ChatRequest(BaseModel):
    agent: str
    text: str
    session_id: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    accepted: bool = True


# noqa C901: FastAPI route-registration factory — nested handlers inflate mccabe count.
def create_app(adapter: "WebAdapter") -> FastAPI:  # noqa: C901
    app = FastAPI(title="Factory Web Smoke", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return _HTML

    @app.get("/api/agents")
    async def list_agents() -> dict[str, list[str]]:
        return {"agents": adapter.agent_names}

    @app.post("/api/chat", response_model=ChatResponse)
    async def post_chat(req: ChatRequest) -> ChatResponse:
        from uuid import uuid4

        session_id = req.session_id or uuid4().hex
        raw = {"agent": req.agent, "text": req.text, "session_id": session_id}
        # Fail fast with HTTP 400 on invalid input *before* the message enters
        # the fire-and-forget pipeline (the in-pipeline parse re-runs normalize).
        try:
            adapter.normalize(raw)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not adapter.ready:
            raise HTTPException(status_code=503, detail="adapter not ready")

        # Route through the shared stage pipeline (parse → ingest → route →
        # session → dispatch), same as Telegram/Discord — the Dispatcher owns
        # the cache_inbound + bus.put via push_to_hub_guarded (ADR-073).
        kit = get_inbound_pipeline_kit()
        parser = get_or_create_parser(kit.parser_cache, adapter, WebWireParser)

        async def _web_backpressure(text: str) -> None:
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
        return ChatResponse(session_id=session_id)

    @app.get("/api/stream/{session_id}")
    async def stream(session_id: str) -> StreamingResponse:
        session = adapter.sessions.get_or_create(session_id)

        async def event_gen() -> AsyncIterator[str]:
            try:
                while not session.closed:
                    try:
                        item = await asyncio.wait_for(
                            session.queue.get(), timeout=120.0
                        )
                    except TimeoutError:
                        yield "data: " + json.dumps({"type": "ping"}) + "\n\n"
                        continue
                    yield "data: " + json.dumps(item) + "\n\n"
                    if item.get("type") in {"done", "error"}:
                        break
            finally:
                # Runs on normal completion AND on client disconnect (generator
                # cancellation) — without this the session leaks in _sessions.
                adapter.sessions.close(session_id)

        return StreamingResponse(event_gen(), media_type="text/event-stream")

    return app


async def run_uvicorn(
    app: FastAPI,
    *,
    host: str,
    port: int,
    server_holder: Any,
) -> None:
    import uvicorn

    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    server_holder._uvicorn_server = server
    await server.serve()
