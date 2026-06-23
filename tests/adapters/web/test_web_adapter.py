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
