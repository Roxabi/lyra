"""RED-phase tests for CliNatsDriver (issue #941).

CliNatsDriver does not exist yet — all tests are expected to fail with
ImportError until the implementation lands in T15.

Covers:
- stream(): yields TextLlmEvent per text chunk, terminates on result chunk
- stream(): propagates is_error from result chunk
- complete(): returns LlmResult on success
- complete(): returns LlmResult with error on worker error
- reset(): dispatches control op with correct payload
- resume_and_reset(): returns ack["resumed"]
- switch_cwd(): dispatches control op with cwd as string
- is_alive(): checks nc.is_connected + _any_worker_alive
- link_lyra_session(): callable without raising
- Class-level constants: HB_SUBJECT, SUBJECT_CMD, SUBJECT_CONTROL

AAA structure throughout.
asyncio_mode = "auto" is configured project-wide in pyproject.toml.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lyra.core.agent.agent_config import ModelConfig
from lyra.core.messaging.events import ResultLlmEvent, TextLlmEvent
from lyra.llm.base import LlmResult

# RED phase — ImportError expected until T15 lands
from lyra.llm.drivers.cli_nats import CliNatsDriver  # type: ignore[import]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_nc(*, is_connected: bool = True) -> MagicMock:
    nc = MagicMock()
    nc.is_connected = is_connected
    nc.new_inbox = MagicMock(return_value="_INBOX.clipool.test")
    nc.subscribe = AsyncMock()
    nc.publish = AsyncMock()
    nc.request = AsyncMock()
    return nc


def _make_driver(nc: MagicMock | None = None, timeout: float = 5.0) -> CliNatsDriver:
    if nc is None:
        nc = _make_nc()
    return CliNatsDriver(nc=nc, timeout=timeout)


def _make_model_cfg() -> ModelConfig:
    return ModelConfig(backend="cli-nats", model="claude-cli")


def _make_reply(data: dict) -> MagicMock:
    msg = MagicMock()
    msg.data = json.dumps(data).encode("utf-8")
    return msg


async def _collect_stream(driver: CliNatsDriver, mock_chunks: list[dict]) -> list:
    """Drive driver.stream() by patching the lower-level _stream_gen."""

    async def _mock_stream_gen(
        subject: str, payload_dict: dict, *, timeout: float | None = None
    ) -> AsyncIterator[dict]:
        for chunk in mock_chunks:
            yield chunk

    events = []
    with patch.object(driver, "_stream_gen", new=_mock_stream_gen):
        async for event in await driver.stream(
            "pool-1", "hello", _make_model_cfg(), "You are helpful."
        ):
            events.append(event)
    return events


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    """Class-level subject constants are correct."""

    def test_hb_subject_set(self) -> None:
        # Arrange / Act / Assert
        assert CliNatsDriver.HB_SUBJECT == "lyra.clipool.heartbeat"

    def test_subject_cmd(self) -> None:
        assert CliNatsDriver.SUBJECT_CMD == "lyra.clipool.cmd"

    def test_subject_control(self) -> None:
        assert CliNatsDriver.SUBJECT_CONTROL == "lyra.clipool.control"


# ---------------------------------------------------------------------------
# stream()
# ---------------------------------------------------------------------------


class TestStream:
    """stream() parses dict chunks from _stream_gen into LlmEvents."""

    @pytest.mark.asyncio
    async def test_stream_yields_text_events(self) -> None:
        """Text chunks are converted to TextLlmEvent."""
        # Arrange
        driver = _make_driver()
        chunks = [
            {"event_type": "text", "text": "hello", "done": False},
            {"event_type": "result", "is_error": False, "done": True},
        ]

        # Act
        events = await _collect_stream(driver, chunks)

        # Assert
        text_events = [e for e in events if isinstance(e, TextLlmEvent)]
        assert len(text_events) == 1
        assert text_events[0].text == "hello"

    @pytest.mark.asyncio
    async def test_stream_terminates_on_result(self) -> None:
        """stream() stops and yields ResultLlmEvent when event_type='result'."""
        # Arrange
        driver = _make_driver()
        chunks = [
            {"event_type": "text", "text": "part1", "done": False},
            {"event_type": "result", "is_error": False, "done": True},
            # This extra chunk must never be reached
            {"event_type": "text", "text": "unreachable", "done": False},
        ]

        # Act
        events = await _collect_stream(driver, chunks)

        # Assert — ResultLlmEvent is last; no events after it
        assert isinstance(events[-1], ResultLlmEvent)
        texts = [e.text for e in events if isinstance(e, TextLlmEvent)]
        assert "unreachable" not in texts

    @pytest.mark.asyncio
    async def test_stream_result_is_error(self) -> None:
        """is_error=True on the result chunk propagates to ResultLlmEvent."""
        # Arrange
        driver = _make_driver()
        chunks = [
            {"event_type": "result", "is_error": True, "done": True},
        ]

        # Act
        events = await _collect_stream(driver, chunks)

        # Assert
        result_events = [e for e in events if isinstance(e, ResultLlmEvent)]
        assert len(result_events) == 1
        assert result_events[0].is_error is True

    @pytest.mark.asyncio
    async def test_stream_result_not_error_by_default(self) -> None:
        """is_error=False on the result chunk yields ResultLlmEvent(is_error=False)."""
        # Arrange
        driver = _make_driver()
        chunks = [
            {"event_type": "result", "is_error": False, "done": True},
        ]

        # Act
        events = await _collect_stream(driver, chunks)

        # Assert
        result_events = [e for e in events if isinstance(e, ResultLlmEvent)]
        assert len(result_events) == 1
        assert result_events[0].is_error is False

    @pytest.mark.asyncio
    async def test_stream_yields_synthetic_result_on_done_non_result_chunk(
        self,
    ) -> None:
        """done=True on a text chunk: text event + synthetic ResultLlmEvent emitted."""
        # Arrange
        driver = _make_driver()
        chunks = [{"event_type": "text", "text": "hello", "done": True}]
        events = []

        async def _mock_stream_gen(
            subject: str, payload_dict: dict, *, timeout: float | None = None
        ) -> AsyncIterator[dict]:
            for c in chunks:
                yield c

        # Act
        with patch.object(driver, "_stream_gen", new=_mock_stream_gen):
            async for ev in await driver.stream(
                "pool-1", "hi", _make_model_cfg(), "sys"
            ):
                events.append(ev)

        # Assert
        assert len(events) == 2
        assert isinstance(events[0], TextLlmEvent)
        assert isinstance(events[1], ResultLlmEvent)
        assert events[1].is_error is False

    @pytest.mark.asyncio
    async def test_stream_calls_stream_gen_with_subject_cmd(self) -> None:
        """stream() delegates to _stream_gen using SUBJECT_CMD."""
        # Arrange
        driver = _make_driver()
        called_subjects: list[str] = []

        async def _spy_stream_gen(
            subject: str, payload_dict: dict, *, timeout: float | None = None
        ) -> AsyncIterator[dict]:
            called_subjects.append(subject)
            yield {"event_type": "result", "is_error": False, "done": True}

        # Act
        with patch.object(driver, "_stream_gen", new=_spy_stream_gen):
            async for _ in await driver.stream("p1", "hi", _make_model_cfg(), "sys"):
                pass

        # Assert
        assert len(called_subjects) == 1
        assert called_subjects[0] == CliNatsDriver.SUBJECT_CMD


# ---------------------------------------------------------------------------
# complete()
# ---------------------------------------------------------------------------


class TestComplete:
    """complete() wraps _request and returns an LlmResult."""

    @pytest.mark.asyncio
    async def test_complete_returns_llm_result(self) -> None:
        """Successful reply is mapped to LlmResult with result text."""
        # Arrange
        nc = _make_nc()
        driver = _make_driver(nc)
        nc.request = AsyncMock(
            return_value=_make_reply(
                {"result": "answer", "session_id": "sid-1", "error": ""}
            )
        )

        # Act
        result = await driver.complete("pool-1", "query", _make_model_cfg(), "sys")

        # Assert
        assert isinstance(result, LlmResult)
        assert result.ok is True
        assert result.result == "answer"

    @pytest.mark.asyncio
    async def test_complete_transport_exception_returns_retryable_error(
        self,
    ) -> None:
        """Transport exception from _request → LlmResult(retryable=True)."""
        # Arrange
        driver = _make_driver()

        async def _mock_request(
            subject: str, payload_dict: dict, *, timeout: float | None = None
        ) -> dict:
            raise Exception("NATS connection lost")

        # Act
        with patch.object(driver, "_request", new=_mock_request):
            result = await driver.complete("pool-1", "hello", _make_model_cfg(), "sys")

        # Assert
        assert result.ok is False
        assert "NATS" in result.error
        assert result.retryable is True

    @pytest.mark.asyncio
    async def test_complete_worker_error_retryable_false(self) -> None:
        """Worker reply with error + retryable=False → LlmResult.retryable is False."""
        # Arrange
        driver = _make_driver()

        async def _mock_request(
            subject: str, payload_dict: dict, *, timeout: float | None = None
        ) -> dict:
            return {"error": "quota exhausted", "retryable": False}

        # Act
        with patch.object(driver, "_request", new=_mock_request):
            result = await driver.complete("pool-1", "hello", _make_model_cfg(), "sys")

        # Assert
        assert result.ok is False
        assert result.retryable is False

    @pytest.mark.asyncio
    async def test_complete_on_error(self) -> None:
        """Worker error in reply yields LlmResult with ok=False."""
        # Arrange
        nc = _make_nc()
        driver = _make_driver(nc)
        nc.request = AsyncMock(
            return_value=_make_reply(
                {"result": "", "session_id": "", "error": "timeout"}
            )
        )

        # Act
        result = await driver.complete("pool-1", "query", _make_model_cfg(), "sys")

        # Assert
        assert result.ok is False
        assert result.error != ""


# ---------------------------------------------------------------------------
# reset()
# ---------------------------------------------------------------------------


class TestReset:
    """reset() dispatches a control message with op='reset'."""

    @pytest.mark.asyncio
    async def test_reset_publishes_control_cmd(self) -> None:
        """reset() calls _request on SUBJECT_CONTROL with op='reset' and pool_id."""
        # Arrange
        driver = _make_driver()
        captured: list[tuple[str, dict]] = []

        async def _mock_request(
            subject: str, payload_dict: dict, *, timeout: float | None = None
        ) -> dict:
            captured.append((subject, payload_dict))
            return {"ok": True}

        # Act
        with patch.object(driver, "_request", new=_mock_request):
            await driver.reset("pool-42")

        # Assert
        assert len(captured) == 1
        subject, payload = captured[0]
        assert subject == CliNatsDriver.SUBJECT_CONTROL
        assert payload.get("op") == "reset"
        assert payload.get("pool_id") == "pool-42"


# ---------------------------------------------------------------------------
# resume_and_reset()
# ---------------------------------------------------------------------------


class TestResumeAndReset:
    """resume_and_reset() returns the value of ack['resumed']."""

    @pytest.mark.asyncio
    async def test_resume_and_reset_returns_ack_true(self) -> None:
        """Returns True when ack['resumed'] is True."""
        # Arrange
        driver = _make_driver()

        async def _mock_request(
            subject: str, payload_dict: dict, *, timeout: float | None = None
        ) -> dict:
            return {"ok": True, "resumed": True}

        # Act
        with patch.object(driver, "_request", new=_mock_request):
            result = await driver.resume_and_reset("pool-1", "sess-abc")

        # Assert
        assert result is True

    @pytest.mark.asyncio
    async def test_resume_and_reset_returns_ack_false(self) -> None:
        """Returns False when ack['resumed'] is False."""
        # Arrange
        driver = _make_driver()

        async def _mock_request(
            subject: str, payload_dict: dict, *, timeout: float | None = None
        ) -> dict:
            return {"ok": True, "resumed": False}

        # Act
        with patch.object(driver, "_request", new=_mock_request):
            result = await driver.resume_and_reset("pool-1", "sess-abc")

        # Assert
        assert result is False

    @pytest.mark.asyncio
    async def test_resume_and_reset_sends_correct_payload(self) -> None:
        """resume_and_reset() sends the TurnStore-resolved cli_sid in the payload.

        The driver resolves lyra_session_id → cli_session_id via TurnStore
        before building the payload. When no TurnStore is wired, session_id=None.
        """
        # Arrange
        driver = _make_driver()
        captured: list[tuple[str, dict]] = []

        async def _mock_request(
            subject: str, payload_dict: dict, *, timeout: float | None = None
        ) -> dict:
            captured.append((subject, payload_dict))
            return {"ok": True, "resumed": True}

        # Act — no TurnStore wired, so cli_sid resolves to None
        with patch.object(driver, "_request", new=_mock_request):
            await driver.resume_and_reset("pool-1", "sess-xyz")

        # Assert
        subject, payload = captured[0]
        assert subject == CliNatsDriver.SUBJECT_CONTROL
        assert payload.get("op") == "resume_and_reset"
        assert payload.get("pool_id") == "pool-1"
        # session_id is the TurnStore-resolved CLI session (None when no store wired)
        assert payload.get("session_id") is None

    @pytest.mark.asyncio
    async def test_resume_and_reset_sends_cli_sid_when_turn_store_resolves(
        self,
    ) -> None:
        """resume_and_reset() sends the TurnStore-resolved cli_sid in the payload."""
        # Arrange
        driver = _make_driver()
        cli_sid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        store = _make_turn_store()
        store.get_cli_session = AsyncMock(return_value=cli_sid)
        driver.set_turn_store(store)
        captured: list[tuple[str, dict]] = []

        async def _mock_request(
            subject: str, payload_dict: dict, *, timeout: float | None = None
        ) -> dict:
            captured.append((subject, payload_dict))
            return {"ok": True, "resumed": True}

        # Act
        with patch.object(driver, "_request", new=_mock_request):
            await driver.resume_and_reset("pool-1", "lyra-sess-xyz")

        # Assert
        _subject, payload = captured[0]
        assert payload.get("session_id") == cli_sid


# ---------------------------------------------------------------------------
# switch_cwd()
# ---------------------------------------------------------------------------


class TestSwitchCwd:
    """switch_cwd() sends cwd as a string via SUBJECT_CONTROL."""

    @pytest.mark.asyncio
    async def test_switch_cwd_sends_cwd(self) -> None:
        """switch_cwd() dispatches op='switch_cwd' with cwd as a string."""
        # Arrange
        driver = _make_driver()
        captured: list[tuple[str, dict]] = []
        cwd = Path("/home/user/project")

        async def _mock_request(
            subject: str, payload_dict: dict, *, timeout: float | None = None
        ) -> dict:
            captured.append((subject, payload_dict))
            return {"ok": True}

        # Act
        with patch.object(driver, "_request", new=_mock_request):
            await driver.switch_cwd("pool-1", cwd)

        # Assert
        subject, payload = captured[0]
        assert subject == CliNatsDriver.SUBJECT_CONTROL
        assert payload.get("op") == "switch_cwd"
        assert payload.get("cwd") == str(cwd)
        assert isinstance(payload.get("cwd"), str)


# ---------------------------------------------------------------------------
# is_alive()
# ---------------------------------------------------------------------------


class TestIsAlive:
    """is_alive() checks nc.is_connected and _any_worker_alive."""

    def test_is_alive_uses_freshness_threshold(self) -> None:
        """is_alive() returns True when connected and a fresh worker exists."""
        # Arrange
        nc = _make_nc(is_connected=True)
        driver = _make_driver(nc)
        # Inject a fresh heartbeat
        driver._worker_freshness["worker-a"] = time.monotonic()

        # Act
        result = driver.is_alive("pool-1")

        # Assert
        assert result is True

    def test_is_alive_false_when_worker_stale(self) -> None:
        """is_alive() returns False when the worker heartbeat is older than HB_TTL."""
        # Arrange
        nc = _make_nc(is_connected=True)
        driver = _make_driver(nc)
        driver._worker_freshness["worker-b"] = time.monotonic() - (driver.HB_TTL + 5.0)

        # Act
        result = driver.is_alive("pool-1")

        # Assert
        assert result is False

    def test_is_alive_false_when_disconnected(self) -> None:
        """is_alive() returns False when nc.is_connected is False."""
        # Arrange
        nc = _make_nc(is_connected=False)
        driver = _make_driver(nc)
        # Even with a fresh worker, disconnected NATS → not alive
        driver._worker_freshness["worker-c"] = time.monotonic()

        # Act
        result = driver.is_alive("pool-1")

        # Assert
        assert result is False

    def test_is_alive_false_when_no_workers(self) -> None:
        """is_alive() returns False when no workers have sent heartbeats."""
        # Arrange
        nc = _make_nc(is_connected=True)
        driver = _make_driver(nc)

        # Act
        result = driver.is_alive("pool-1")

        # Assert
        assert result is False


# ---------------------------------------------------------------------------
# link_lyra_session()
# ---------------------------------------------------------------------------


class TestLinkLyraSession:
    """link_lyra_session() is callable without raising."""

    def test_link_lyra_session_does_not_raise(self) -> None:
        """link_lyra_session() is a no-op or state mutation — must not raise."""
        # Arrange
        driver = _make_driver()

        # Act / Assert
        driver.link_lyra_session("pool-1", "lyra-session-abc")


# ---------------------------------------------------------------------------
# Session lifecycle — lyra_session_id propagation (#1008)
# ---------------------------------------------------------------------------


def _make_turn_store() -> AsyncMock:
    """Build a minimal _CliSessionStore mock."""
    store = AsyncMock()
    store.set_cli_session = AsyncMock()
    store.get_cli_session = AsyncMock(return_value=None)
    return store


class TestLinkLyraSessionMapping:
    """link_lyra_session() stores mapping used by _build_cmd_payload."""

    def test_link_stores_mapping_and_payload_uses_uuid(self) -> None:
        """After link_lyra_session, _build_cmd_payload uses the lyra UUID."""
        # Arrange
        driver = _make_driver()
        lyra_uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

        # Act
        driver.link_lyra_session("pool-1", lyra_uuid)
        payload = driver._build_cmd_payload(
            "pool-1", "hello", _make_model_cfg(), "sys", stream=False
        )

        # Assert — lyra_session_id in payload is the linked UUID, not the pool_id
        assert payload["lyra_session_id"] == lyra_uuid
        assert payload["lyra_session_id"] != "pool-1"

    def test_build_cmd_payload_falls_back_to_pool_id_when_no_link(self) -> None:
        """_build_cmd_payload falls back to pool_id when no link registered."""
        # Arrange
        driver = _make_driver()

        # Act — no link_lyra_session call
        payload = driver._build_cmd_payload(
            "pool-xyz", "text", _make_model_cfg(), "sys", stream=False
        )

        # Assert — no KeyError; falls back gracefully
        assert payload["lyra_session_id"] == "pool-xyz"


class TestCompleteSessionPersistence:
    """complete() persists cli_session_id via TurnStore when wired."""

    @pytest.mark.asyncio
    async def test_complete_calls_set_cli_session_when_turn_store_wired(self) -> None:
        """complete() fires set_cli_session(lyra_sid, cli_sid) when TurnStore is set."""
        # Arrange
        driver = _make_driver()
        lyra_uuid = "11111111-2222-3333-4444-555555555555"
        cli_sid = "cli-xyz"
        driver.link_lyra_session("pool-1", lyra_uuid)

        store = _make_turn_store()
        driver.set_turn_store(store)

        async def _mock_request(
            subject: str, payload_dict: dict, *, timeout: float | None = None
        ) -> dict:
            return {"result": "ok", "session_id": cli_sid, "error": ""}

        # Act
        with patch.object(driver, "_request", new=_mock_request):
            result = await driver.complete("pool-1", "hello", _make_model_cfg(), "sys")

        # Assert — result ok
        assert result.ok is True

        # Allow the fire-and-forget task to complete
        import asyncio

        await asyncio.sleep(0)

        # Assert — set_cli_session was called with (lyra_uuid, cli_sid)
        store.set_cli_session.assert_awaited_once_with(lyra_uuid, cli_sid)

    @pytest.mark.asyncio
    async def test_complete_no_turn_store_write_when_store_is_none(self) -> None:
        """complete() skips TurnStore write when _turn_store is None (no error)."""
        # Arrange
        driver = _make_driver()
        driver.link_lyra_session("pool-1", "lyra-abc")

        # _turn_store stays None (default)

        async def _mock_request(
            subject: str, payload_dict: dict, *, timeout: float | None = None
        ) -> dict:
            return {"result": "ok", "session_id": "cli-xyz", "error": ""}

        # Act / Assert — no AttributeError raised
        with patch.object(driver, "_request", new=_mock_request):
            result = await driver.complete("pool-1", "hello", _make_model_cfg(), "sys")

        assert result.ok is True

    @pytest.mark.asyncio
    async def test_set_turn_store_none_is_safe(self) -> None:
        """set_turn_store(None) does not raise and leaves _turn_store as None."""
        # Arrange
        driver = _make_driver()
        store = _make_turn_store()
        driver.set_turn_store(store)

        # Act — reset to None
        driver.set_turn_store(None)  # type: ignore[arg-type]

        # Assert
        assert driver._turn_store is None


class TestStreamGenSessionPersistence:
    """_stream_gen_llm() persists cli_session_id when result chunk has session_id."""

    @pytest.mark.asyncio
    async def test_stream_gen_calls_set_cli_session_on_result_chunk(self) -> None:
        """_stream_gen_llm yields ResultLlmEvent and fires set_cli_session task."""
        # Arrange
        driver = _make_driver()
        lyra_uuid = "aaaaaaaa-0000-0000-0000-000000000001"
        cli_sid = "cccccccc-dddd-dddd-dddd-dddddddddddd"
        driver.link_lyra_session("pool-1", lyra_uuid)

        store = _make_turn_store()
        driver.set_turn_store(store)

        chunks = [
            {"event_type": "text", "text": "hi", "done": False},
            {
                "event_type": "result",
                "is_error": False,
                "session_id": cli_sid,
                "done": True,
            },
        ]

        events = []

        async def _mock_stream_gen(
            subject: str, payload_dict: dict, *, timeout: float | None = None
        ) -> AsyncIterator[dict]:
            for c in chunks:
                yield c

        # Act
        with patch.object(driver, "_stream_gen", new=_mock_stream_gen):
            async for ev in await driver.stream(
                "pool-1", "hello", _make_model_cfg(), "sys"
            ):
                events.append(ev)

        import asyncio

        await asyncio.sleep(0)

        # Assert — ResultLlmEvent carries session_id
        result_events = [e for e in events if isinstance(e, ResultLlmEvent)]
        assert len(result_events) == 1
        assert result_events[0].session_id == cli_sid

        # Assert — set_cli_session persisted the mapping
        store.set_cli_session.assert_awaited_once_with(lyra_uuid, cli_sid)

    @pytest.mark.asyncio
    async def test_stream_gen_no_set_cli_session_when_turn_store_none(self) -> None:
        """_stream_gen_llm does not error when _turn_store is None."""
        # Arrange
        driver = _make_driver()
        driver.link_lyra_session("pool-1", "lyra-uuid-xyz")
        # _turn_store is None by default

        chunks = [
            {
                "event_type": "result",
                "is_error": False,
                "session_id": "cli-abc",
                "done": True,
            }
        ]

        async def _mock_stream_gen(
            subject: str, payload_dict: dict, *, timeout: float | None = None
        ) -> AsyncIterator[dict]:
            for c in chunks:
                yield c

        # Act / Assert — no AttributeError
        with patch.object(driver, "_stream_gen", new=_mock_stream_gen):
            async for _ in await driver.stream(
                "pool-1", "hi", _make_model_cfg(), "sys"
            ):
                pass
