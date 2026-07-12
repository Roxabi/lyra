"""Unit tests for the web smoke adapter."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from factory.adapters.web.web_adapter import WEB_BOT_ID, WebAdapter
from factory.adapters.web.web_formatter import WebFormatter
from factory.adapters.web.web_sessions import WebSessionHub
from factory.core.auth.trust import TrustLevel
from factory.core.messaging.message import (
    InboundMessage,
    OutboundMessage,
    WebMeta,
)
from factory.core.messaging.render_events import (
    ReasoningDeltaRenderEvent,
    ReasoningEndRenderEvent,
    ReasoningStartRenderEvent,
)


def _make_adapter() -> WebAdapter:
    bus = MagicMock()
    bus.put = AsyncMock()
    return WebAdapter(
        bot_id=WEB_BOT_ID,
        inbound_bus=bus,
        agent_names=["lyra_default", "aryl_default"],
        port=18765,
    )


class TestWebNormalize:
    def test_normalize_builds_agent_scope(self) -> None:
        adapter = _make_adapter()
        msg = adapter.normalize(
            {"agent": "lyra_default", "text": "hello", "session_id": "sess-1"}
        )
        assert msg.platform == "web"
        assert msg.bot_id == WEB_BOT_ID
        assert msg.scope_id == "agent:lyra_default"
        assert msg.platform_meta == WebMeta(session_id="sess-1")

    def test_normalize_rejects_unknown_agent(self) -> None:
        adapter = _make_adapter()
        with pytest.raises(ValueError, match="unknown agent"):
            adapter.normalize({"agent": "ghost", "text": "hi"})


class TestWebOutbound:
    async def test_send_publishes_done(self) -> None:
        adapter = _make_adapter()
        inbound = InboundMessage(
            id="1",
            platform="web",
            bot_id=WEB_BOT_ID,
            scope_id="agent:lyra_default",
            user_id="smoke",
            user_name="Smoke",
            is_mention=True,
            text="hi",
            text_raw="hi",
            timestamp=datetime.now(timezone.utc),
            platform_meta=WebMeta(session_id="sess-42"),
            trust_level=TrustLevel.TRUSTED,
        )
        outbound = OutboundMessage.from_text("pong")
        await adapter.send(inbound, outbound)
        session = adapter.sessions.get_or_create("sess-42")
        events = []
        while not session.queue.empty():
            events.append(session.queue.get_nowait())
        assert events[0]["text"] == "pong"
        assert events[-1]["type"] == "done"


class TestWebFormatter:
    async def test_edit_placeholder_pushes_delta(self) -> None:
        hub = WebSessionHub()
        fmt = WebFormatter(hub, "s1")
        await fmt.edit_placeholder_text("s1", "partial")
        item = hub.get_or_create("s1").queue.get_nowait()
        assert item == {"type": "delta", "text": "partial"}

    async def test_edit_placeholder_finalize_emits_done(self) -> None:
        # Streaming terminal: the "done" SSE sentinel now comes from the
        # formatter's finalize edit, not a send_streaming override (ADR-073).
        hub = WebSessionHub()
        fmt = WebFormatter(hub, "s2")
        await fmt.edit_placeholder_text("s2", "final", finalize=True)
        q = hub.get_or_create("s2").queue
        items = []
        while not q.empty():
            items.append(q.get_nowait())
        assert items[0] == {"type": "delta", "text": "final"}
        assert items[-1] == {"type": "done"}

    async def test_send_fallback_emits_done(self) -> None:
        hub = WebSessionHub()
        fmt = WebFormatter(hub, "s3")
        await fmt.send_fallback("oops")
        q = hub.get_or_create("s3").queue
        items = []
        while not q.empty():
            items.append(q.get_nowait())
        assert items[-1] == {"type": "done"}


class TestWebFormatterAgui:
    def _agui_hub(self) -> WebSessionHub:
        hub = WebSessionHub()
        hub.set_stream_format("s-agui", "agui")
        return hub

    async def test_incremental_text_deltas(self) -> None:
        hub = self._agui_hub()
        fmt = WebFormatter(hub, "s-agui")
        await fmt.send_placeholder()
        await fmt.edit_placeholder_text("s-agui", "hel")
        await fmt.edit_placeholder_text("s-agui", "hello", finalize=True)
        q = hub.get_or_create("s-agui").queue
        items = []
        while not q.empty():
            items.append(q.get_nowait())
        assert items[0]["type"] == "RUN_STARTED"
        assert items[1]["type"] == "TEXT_MESSAGE_START"
        content = [i for i in items if i["type"] == "TEXT_MESSAGE_CONTENT"]
        assert [i["delta"] for i in content] == ["hel", "lo"]
        assert items[-2]["type"] == "TEXT_MESSAGE_END"
        assert items[-1]["type"] == "RUN_FINISHED"

    async def test_reasoning_events(self) -> None:
        hub = self._agui_hub()
        fmt = WebFormatter(hub, "s-agui")
        mid = "reason-1"
        await fmt.edit_reasoning(None, ReasoningStartRenderEvent(message_id=mid))
        await fmt.edit_reasoning(
            None, ReasoningDeltaRenderEvent(message_id=mid, delta="think")
        )
        await fmt.edit_reasoning(None, ReasoningEndRenderEvent(message_id=mid))
        q = hub.get_or_create("s-agui").queue
        types = []
        while not q.empty():
            types.append(q.get_nowait()["type"])
        assert types == [
            "REASONING_START",
            "REASONING_MESSAGE_START",
            "REASONING_MESSAGE_CONTENT",
            "REASONING_MESSAGE_END",
            "REASONING_END",
        ]

    async def test_send_fallback_agui_terminal(self) -> None:
        hub = self._agui_hub()
        fmt = WebFormatter(hub, "s-agui")
        await fmt.send_fallback("oops")
        q = hub.get_or_create("s-agui").queue
        items = []
        while not q.empty():
            items.append(q.get_nowait())
        assert items[-1]["type"] == "RUN_FINISHED"


class TestWebOutboundAgui:
    async def test_send_publishes_agui_sequence(self) -> None:
        adapter = _make_adapter()
        adapter.sessions.set_stream_format("sess-agui", "agui")
        inbound = InboundMessage(
            id="1",
            platform="web",
            bot_id=WEB_BOT_ID,
            scope_id="agent:lyra_default",
            user_id="smoke",
            user_name="Smoke",
            is_mention=True,
            text="hi",
            text_raw="hi",
            timestamp=datetime.now(timezone.utc),
            platform_meta=WebMeta(session_id="sess-agui"),
            trust_level=TrustLevel.TRUSTED,
        )
        outbound = OutboundMessage.from_text("pong")
        await adapter.send(inbound, outbound)
        session = adapter.sessions.get_or_create("sess-agui")
        events = []
        while not session.queue.empty():
            events.append(session.queue.get_nowait())
        assert events[0]["type"] == "RUN_STARTED"
        assert events[1]["type"] == "TEXT_MESSAGE_START"
        assert events[2] == {
            "type": "TEXT_MESSAGE_CONTENT",
            "messageId": events[2]["messageId"],
            "delta": "pong",
        }
        assert events[3]["type"] == "TEXT_MESSAGE_END"
        assert events[4]["type"] == "RUN_FINISHED"


class TestChatStreamFormat:
    @pytest.fixture
    def chat_client(self, monkeypatch: pytest.MonkeyPatch) -> tuple:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from factory.adapters.web.chat_routes import build_chat_router
        from factory.dashboard.stream_tokens import StreamTokenRegistry

        monkeypatch.setenv("FACTORY_DASHBOARD_OPERATOR_TOKEN", "test-op-token")
        monkeypatch.delenv("FACTORY_DASHBOARD_E2E", raising=False)
        adapter = _make_adapter()
        tokens = StreamTokenRegistry()
        app = FastAPI()
        app.include_router(build_chat_router(adapter, tokens))
        client = TestClient(app)
        client.headers.update({"Authorization": "Bearer test-op-token"})
        return client, adapter, tokens

    def test_stream_format_mismatch_returns_403(self, chat_client: tuple) -> None:
        client, adapter, tokens = chat_client
        session_id = "sess-format-mismatch"
        adapter.sessions.set_stream_format(session_id, "agui")
        token = tokens.mint(session_id)
        resp = client.get(
            f"/api/stream/{session_id}?token={token}&format=legacy",
        )
        assert resp.status_code == 403
        assert resp.json()["detail"] == "stream format mismatch"

    def test_stream_format_match_allows_subscribe(self, chat_client: tuple) -> None:
        client, adapter, tokens = chat_client
        session_id = "sess-format-ok"
        adapter.sessions.set_stream_format(session_id, "agui")
        token = tokens.mint(session_id)
        adapter.sessions.get_or_create(session_id).queue.put_nowait(
            {"type": "RUN_FINISHED", "threadId": session_id, "runId": "r1"},
        )
        with client.stream(
            "GET",
            f"/api/stream/{session_id}?token={token}&format=agui",
        ) as resp:
            assert resp.status_code == 200
            body = "".join(resp.iter_text())
        assert "RUN_FINISHED" in body

    def test_post_chat_sets_agui_format(self, chat_client: tuple) -> None:
        client, adapter, _tokens = chat_client
        listener = MagicMock()
        listener.cache_inbound = MagicMock()
        adapter._outbound_listener = listener
        res = client.post(
            "/api/chat?format=agui",
            json={"agent": "lyra_default", "text": "hi"},
        )
        assert res.status_code == 200
        body = res.json()
        session_id = body["session_id"]
        assert adapter.sessions.get_or_create(session_id).stream_format == "agui"
        token = body["stream_token"]
        adapter.sessions.get_or_create(session_id).queue.put_nowait(
            {"type": "RUN_FINISHED", "threadId": session_id, "runId": "r1"},
        )
        with client.stream(
            "GET",
            f"/api/stream/{session_id}?token={token}&format=agui",
        ) as stream_resp:
            assert stream_resp.status_code == 200
            text = "".join(stream_resp.iter_text())
        assert "RUN_FINISHED" in text

    def test_post_chat_rejects_unknown_agent_before_format_set(
        self, chat_client: tuple
    ) -> None:
        client, adapter, _tokens = chat_client
        session_id = "sess-bad-agent"
        res = client.post(
            "/api/chat?format=agui",
            json={"agent": "nope", "text": "hi", "session_id": session_id},
        )
        assert res.status_code == 400
        assert adapter.sessions.get_or_create(session_id).stream_format == "legacy"
