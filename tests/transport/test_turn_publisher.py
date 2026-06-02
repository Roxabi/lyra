"""T7 tests — TurnPublisher: publish_* methods and JetStream PubAck await.

Covers:
- publish_log_turn: correct subject, bytes payload, kind/fields round-trip
- publish_start_session: kind == "start_session"
- publish_end_session: kind == "end_session"
- publish_set_cli_session: kind == "set_cli_session", cli_session_id propagated
- publish_increment_resume_count: kind == "increment_resume_count",
  target_count propagated
- PubAck await: js.publish AsyncMock is awaited exactly once per call
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from factory.transport.turn_publisher import TurnPublisher
from roxabi_contracts.turns import SUBJECTS, TurnWriteEvent

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

_POOL_ID = "pool:tg:chat:99"
_SESSION_ID = "sess-deadbeef"
_PLATFORM = "telegram"
_USER_ID = "user:42"
_TRACE_ID = "a1b2c3d4-0000-0000-0000-000000000001"


def _make_publisher() -> tuple[TurnPublisher, AsyncMock]:
    """Return (publisher, mock_js) with js.publish as an AsyncMock."""
    mock_js = MagicMock()
    mock_js.publish = AsyncMock(return_value=MagicMock())
    publisher = TurnPublisher(mock_js)
    return publisher, mock_js


def _decode_published_event(mock_js: AsyncMock) -> TurnWriteEvent:
    """Extract and deserialise the TurnWriteEvent from the first publish call."""
    assert mock_js.publish.call_count == 1
    call_args = mock_js.publish.call_args
    subject, raw = call_args[0]
    assert subject == SUBJECTS.turn_write
    assert isinstance(raw, bytes)
    data = json.loads(raw.decode("utf-8"))
    return TurnWriteEvent.model_validate(data)


# ---------------------------------------------------------------------------
# A. publish_log_turn
# ---------------------------------------------------------------------------


class TestPublishLogTurn:
    @pytest.mark.anyio
    async def test_publish_log_turn_calls_js_publish_once(self) -> None:
        publisher, mock_js = _make_publisher()
        await publisher.publish_log_turn(
            pool_id=_POOL_ID,
            session_id=_SESSION_ID,
            platform=_PLATFORM,
            user_id=_USER_ID,
            role="user",
            content="hello world",
            message_id="msg-001",
            reply_message_id=None,
            metadata={},
            trace_id=_TRACE_ID,
        )
        mock_js.publish.assert_called_once()

    @pytest.mark.anyio
    async def test_publish_log_turn_subject_and_bytes(self) -> None:
        publisher, mock_js = _make_publisher()
        await publisher.publish_log_turn(
            pool_id=_POOL_ID,
            session_id=_SESSION_ID,
            platform=_PLATFORM,
            user_id=_USER_ID,
            role="assistant",
            content="response text",
            message_id="msg-002",
            trace_id=_TRACE_ID,
        )
        call_args = mock_js.publish.call_args[0]
        subject, raw = call_args
        assert subject == "lyra.turns.write"
        assert isinstance(raw, bytes)

    @pytest.mark.anyio
    async def test_publish_log_turn_payload_kind_and_fields(self) -> None:
        publisher, mock_js = _make_publisher()
        await publisher.publish_log_turn(
            pool_id=_POOL_ID,
            session_id=_SESSION_ID,
            platform=_PLATFORM,
            user_id=_USER_ID,
            role="user",
            content="check fields",
            message_id="msg-003",
            reply_message_id="msg-000",
            metadata={"extra": "val"},
            trace_id=_TRACE_ID,
        )
        event = _decode_published_event(mock_js)
        assert event.payload.kind == "log_turn"
        assert event.pool_id == _POOL_ID
        assert event.session_id == _SESSION_ID
        assert event.platform == _PLATFORM
        assert event.user_id == _USER_ID
        assert event.payload.role == "user"  # type: ignore[union-attr]
        assert event.payload.content == "check fields"  # type: ignore[union-attr]
        assert event.payload.message_id == "msg-003"  # type: ignore[union-attr]
        assert event.payload.reply_message_id == "msg-000"  # type: ignore[union-attr]
        assert event.payload.metadata == {"extra": "val"}  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# B. publish_start_session
# ---------------------------------------------------------------------------


class TestPublishStartSession:
    @pytest.mark.anyio
    async def test_publish_start_session_kind(self) -> None:
        publisher, mock_js = _make_publisher()
        await publisher.publish_start_session(
            pool_id=_POOL_ID,
            session_id=_SESSION_ID,
            platform=_PLATFORM,
            user_id=_USER_ID,
            trace_id=_TRACE_ID,
        )
        event = _decode_published_event(mock_js)
        assert event.payload.kind == "start_session"
        assert event.pool_id == _POOL_ID
        assert event.session_id == _SESSION_ID
        assert event.platform == _PLATFORM
        assert event.user_id == _USER_ID

    @pytest.mark.anyio
    async def test_publish_start_session_subject(self) -> None:
        publisher, mock_js = _make_publisher()
        await publisher.publish_start_session(
            pool_id=_POOL_ID,
            session_id=_SESSION_ID,
            platform=_PLATFORM,
            user_id=_USER_ID,
            trace_id=_TRACE_ID,
        )
        subject = mock_js.publish.call_args[0][0]
        assert subject == "lyra.turns.write"


# ---------------------------------------------------------------------------
# C. publish_end_session
# ---------------------------------------------------------------------------


class TestPublishEndSession:
    @pytest.mark.anyio
    async def test_publish_end_session_kind(self) -> None:
        publisher, mock_js = _make_publisher()
        await publisher.publish_end_session(
            pool_id=_POOL_ID,
            session_id=_SESSION_ID,
            platform=_PLATFORM,
            user_id=_USER_ID,
            trace_id=_TRACE_ID,
        )
        event = _decode_published_event(mock_js)
        assert event.payload.kind == "end_session"
        assert event.pool_id == _POOL_ID
        assert event.session_id == _SESSION_ID

    @pytest.mark.anyio
    async def test_publish_end_session_subject(self) -> None:
        publisher, mock_js = _make_publisher()
        await publisher.publish_end_session(
            pool_id=_POOL_ID,
            session_id=_SESSION_ID,
            platform=_PLATFORM,
            user_id=_USER_ID,
            trace_id=_TRACE_ID,
        )
        subject = mock_js.publish.call_args[0][0]
        assert subject == "lyra.turns.write"


# ---------------------------------------------------------------------------
# D. publish_set_cli_session
# ---------------------------------------------------------------------------


class TestPublishSetCliSession:
    @pytest.mark.anyio
    async def test_publish_set_cli_session_kind_and_id(self) -> None:
        publisher, mock_js = _make_publisher()
        await publisher.publish_set_cli_session(
            pool_id=_POOL_ID,
            session_id=_SESSION_ID,
            platform=_PLATFORM,
            user_id=_USER_ID,
            cli_session_id="cli-sess-xyz",
            trace_id=_TRACE_ID,
        )
        event = _decode_published_event(mock_js)
        assert event.payload.kind == "set_cli_session"
        assert event.payload.cli_session_id == "cli-sess-xyz"  # type: ignore[union-attr]
        assert event.pool_id == _POOL_ID
        assert event.session_id == _SESSION_ID

    @pytest.mark.anyio
    async def test_publish_set_cli_session_subject(self) -> None:
        publisher, mock_js = _make_publisher()
        await publisher.publish_set_cli_session(
            pool_id=_POOL_ID,
            session_id=_SESSION_ID,
            platform=_PLATFORM,
            user_id=_USER_ID,
            cli_session_id="cli-sess-abc",
            trace_id=_TRACE_ID,
        )
        subject = mock_js.publish.call_args[0][0]
        assert subject == "lyra.turns.write"


# ---------------------------------------------------------------------------
# E. publish_increment_resume_count
# ---------------------------------------------------------------------------


class TestPublishIncrementResumeCount:
    @pytest.mark.anyio
    async def test_publish_increment_resume_count_kind_and_count(self) -> None:
        publisher, mock_js = _make_publisher()
        await publisher.publish_increment_resume_count(
            pool_id=_POOL_ID,
            session_id=_SESSION_ID,
            platform=_PLATFORM,
            user_id=_USER_ID,
            target_count=3,
            trace_id=_TRACE_ID,
        )
        event = _decode_published_event(mock_js)
        assert event.payload.kind == "increment_resume_count"
        assert event.payload.target_count == 3  # type: ignore[union-attr]
        assert event.pool_id == _POOL_ID
        assert event.session_id == _SESSION_ID

    @pytest.mark.anyio
    async def test_publish_increment_resume_count_subject(self) -> None:
        publisher, mock_js = _make_publisher()
        await publisher.publish_increment_resume_count(
            pool_id=_POOL_ID,
            session_id=_SESSION_ID,
            platform=_PLATFORM,
            user_id=_USER_ID,
            target_count=7,
            trace_id=_TRACE_ID,
        )
        subject = mock_js.publish.call_args[0][0]
        assert subject == "lyra.turns.write"


# ---------------------------------------------------------------------------
# F. PubAck await
# ---------------------------------------------------------------------------


class TestPublishAwaitsPubAck:
    @pytest.mark.anyio
    async def test_publish_awaits_pubAck(self) -> None:
        """js.publish AsyncMock is awaited exactly once per publish_* call."""
        from nats.js.api import PubAck

        fake_ack = PubAck(stream="TURNS", seq=42)
        mock_js = MagicMock()
        mock_js.publish = AsyncMock(return_value=fake_ack)
        publisher = TurnPublisher(mock_js)

        await publisher.publish_start_session(
            pool_id=_POOL_ID,
            session_id=_SESSION_ID,
            platform=_PLATFORM,
            user_id=_USER_ID,
            trace_id=_TRACE_ID,
        )

        # AsyncMock records awaits separately from calls — both must be 1
        assert mock_js.publish.call_count == 1
        assert mock_js.publish.await_count == 1
