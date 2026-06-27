"""Dashboard contract round-trip tests (#1771)."""

from __future__ import annotations

from roxabi_contracts.dashboard import (
    ChatRequest,
    ChatResponse,
    DashboardSessionsListRequest,
    SseEvent,
)


def test_chat_request_roundtrip() -> None:
    req = ChatRequest(agent="lyra", text="hi", harness="claude-cli")
    parsed = ChatRequest.model_validate_json(req.model_dump_json())
    assert parsed.agent == "lyra"


def test_chat_response_has_stream_token() -> None:
    res = ChatResponse(session_id="s1", stream_token="tok")
    assert res.stream_token == "tok"


def test_sse_event_types() -> None:
    ev = SseEvent(type="delta", text="x")
    assert ev.type == "delta"


def test_sessions_list_request_limit_bounds() -> None:
    req = DashboardSessionsListRequest(agent="lyra", limit=10)
    assert req.limit == 10