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

import asyncio
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import nats.errors
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
    """Drive driver.stream() by patching the lower-level _dict_stream_gen."""

    async def _mock_dict_stream_gen(
        subject: str, payload_dict: dict, *, timeout: float | None = None
    ) -> AsyncIterator[dict]:
        for chunk in mock_chunks:
            yield chunk

    events = []
    with patch.object(driver, "_dict_stream_gen", new=_mock_dict_stream_gen):
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
    """stream() parses dict chunks from _dict_stream_gen into LlmEvents."""

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

        async def _mock_dict_stream_gen(
            subject: str, payload_dict: dict, *, timeout: float | None = None
        ) -> AsyncIterator[dict]:
            for c in chunks:
                yield c

        # Act
        with patch.object(driver, "_dict_stream_gen", new=_mock_dict_stream_gen):
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
    async def test_stream_calls_dict_stream_gen_with_subject_cmd(self) -> None:
        """stream() delegates to _dict_stream_gen using SUBJECT_CMD."""
        # Arrange
        driver = _make_driver()
        called_subjects: list[str] = []

        async def _spy_dict_stream_gen(
            subject: str, payload_dict: dict, *, timeout: float | None = None
        ) -> AsyncIterator[dict]:
            called_subjects.append(subject)
            yield {"event_type": "result", "is_error": False, "done": True}

        # Act
        with patch.object(driver, "_dict_stream_gen", new=_spy_dict_stream_gen):
            async for _ in await driver.stream("p1", "hi", _make_model_cfg(), "sys"):
                pass

        # Assert
        assert len(called_subjects) == 1
        assert called_subjects[0] == CliNatsDriver.SUBJECT_CMD

    @pytest.mark.asyncio
    async def test_stream_transport_error_sanitizes_exc_message(self) -> None:
        """#1256 — str(exc) sentinel must NOT surface in ResultLlmEvent.error_text.

        Mirrors the sibling sanitization rule enforced for ``complete()`` (#1253)
        and the streaming path in ``nats_llm_client`` (#1212). NATS errors can
        embed server addresses / connection metadata via ``str(exc)``; the bus
        boundary must only carry the class name.

        Uses ``nats.errors.Error`` (the base class) specifically because its
        ``__str__`` passes the constructor argument through unchanged — so the
        sentinel-not-in-error_text assertion actually falsifies a revert to
        ``str(exc)``. ``NoRespondersError`` and ``TimeoutError`` override
        ``__str__`` with hardcoded messages that ignore constructor args, which
        would make the sentinel assertion vacuous for those subclasses (their
        class-name dispatch is covered separately below).
        """
        # Arrange
        sentinel = "goose-logarithm.ts.net:4222-LEAK-SENTINEL"
        driver = _make_driver()

        async def _raising_dict_stream_gen(
            subject: str, payload_dict: dict, *, timeout: float | None = None
        ) -> AsyncIterator[dict]:
            raise nats.errors.Error(sentinel)
            yield  # pragma: no cover — make this an async generator

        events: list = []

        # Act
        with (
            patch("lyra.llm.drivers.cli_nats.log") as mock_log,
            patch.object(driver, "_dict_stream_gen", new=_raising_dict_stream_gen),
        ):
            async for ev in await driver.stream(
                "pool-1", "hi", _make_model_cfg(), "sys"
            ):
                events.append(ev)

        # Assert — exactly one terminal error event
        assert len(events) == 1
        result = events[0]
        assert isinstance(result, ResultLlmEvent)
        assert result.is_error is True
        # Sanitization: error_text must NOT contain str(exc) sentinel ...
        assert result.error_text is not None
        assert sentinel not in result.error_text
        # ... and MUST match the sanitized class-name form (falsifies regressions
        # to ``f"NATS transport error: {exc}"`` or ``str(exc)``).
        assert result.error_text == "NATS transport error: Error"
        # WorkerError envelope is co-populated per ResultLlmEvent contract.
        assert result.worker_error is not None
        assert result.worker_error.code == "transport.error"
        assert result.worker_error.message == result.error_text
        assert result.worker_error.retryable is True
        # Log side: %r format keeps the sentinel out of the format-string
        # itself (the args carry repr(exc), never str(exc) — guards against a
        # silent %r → %s revert that would re-introduce the leak in log sinks).
        mock_log.warning.assert_called_once()
        log_format = mock_log.warning.call_args.args[0]
        assert "%r" in log_format
        assert sentinel not in log_format

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("exc_class", "expected_name"),
        [
            (nats.errors.NoRespondersError, "NoRespondersError"),
            (nats.errors.TimeoutError, "TimeoutError"),
        ],
    )
    async def test_stream_transport_error_encodes_subclass_name(
        self, exc_class: type[nats.errors.Error], expected_name: str
    ) -> None:
        """#1256 — class-name dispatch survives nats.errors subclass propagation.

        Separate from the sentinel-leak test because ``NoRespondersError`` and
        ``TimeoutError`` both override ``__str__`` with hardcoded class messages
        that ignore constructor args — making a sentinel-not-in-error_text
        assertion vacuous for them. Here we verify only what is real for those
        subclasses: the production code uses ``type(exc).__name__``, so the
        terminal ``error_text`` carries the precise subclass name.
        """
        # Arrange
        driver = _make_driver()

        async def _raising_dict_stream_gen(
            subject: str, payload_dict: dict, *, timeout: float | None = None
        ) -> AsyncIterator[dict]:
            raise exc_class()
            yield  # pragma: no cover — make this an async generator

        events: list = []

        # Act
        with patch.object(driver, "_dict_stream_gen", new=_raising_dict_stream_gen):
            async for ev in await driver.stream(
                "pool-1", "hi", _make_model_cfg(), "sys"
            ):
                events.append(ev)

        # Assert
        assert len(events) == 1
        result = events[0]
        assert isinstance(result, ResultLlmEvent)
        assert result.error_text == f"NATS transport error: {expected_name}"

    @pytest.mark.asyncio
    async def test_stream_transport_error_after_partial_yield(self) -> None:
        """#1256 — mid-stream NATS error still yields a terminal sanitized event.

        Confirms the ``try/except`` wraps the entire ``async for`` body: a text
        chunk delivered before the failure surfaces to the consumer, followed
        by a single terminal ``ResultLlmEvent(is_error=True)``.
        """
        # Arrange
        sentinel = "goose-logarithm.ts.net:4222-MID-STREAM"
        driver = _make_driver()

        async def _partial_then_raise(
            subject: str, payload_dict: dict, *, timeout: float | None = None
        ) -> AsyncIterator[dict]:
            yield {"event_type": "text", "text": "partial", "done": False}
            raise nats.errors.Error(sentinel)

        events: list = []

        # Act
        with patch.object(driver, "_dict_stream_gen", new=_partial_then_raise):
            async for ev in await driver.stream(
                "pool-1", "hi", _make_model_cfg(), "sys"
            ):
                events.append(ev)

        # Assert — partial text delivered, then a sanitized terminal event
        assert len(events) == 2
        assert isinstance(events[0], TextLlmEvent)
        assert events[0].text == "partial"
        terminal = events[1]
        assert isinstance(terminal, ResultLlmEvent)
        assert terminal.is_error is True
        assert terminal.error_text == "NATS transport error: Error"
        assert sentinel not in (terminal.error_text or "")
        # WorkerError envelope mirrors the partial-yield-absent path —
        # asymmetric coverage on the mid-stream path would let a future
        # refactor silently drop the envelope.
        assert terminal.worker_error is not None
        assert terminal.worker_error.code == "transport.error"
        assert terminal.worker_error.message == terminal.error_text
        assert terminal.worker_error.retryable is True


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
            raise nats.errors.Error("NATS connection lost")

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

    @pytest.mark.asyncio
    async def test_complete_transport_error_does_not_leak_exc_str_to_bus(
        self,
    ) -> None:
        """nats.errors.Error.__str__ must NOT appear in LlmResult.error (#1253).

        nats.errors.Error.__str__ can embed server addresses / connection
        metadata. LlmResult.error is bus-bound and may reach user-visible
        renders. Only type(exc).__name__ is permitted on the bus.

        Falsification: if the fix is reverted to f"NATS transport error: {exc}"
        the sentinel host token present in str(exc) leaks into result.error and
        this test fails.
        """
        # Arrange — sentinel embeds a host token that must NOT surface on bus
        _SENSITIVE_TOKEN = "goose-logarithm.ts.net:4222"
        driver = _make_driver()

        async def _mock_request(
            subject: str, payload_dict: dict, *, timeout: float | None = None
        ) -> dict:
            raise nats.errors.Error(f"connection refused: {_SENSITIVE_TOKEN}")

        # Act
        with patch.object(driver, "_request", new=_mock_request):
            result = await driver.complete("pool-1", "hello", _make_model_cfg(), "sys")

        # Assert — sentinel did not leak onto the bus
        assert _SENSITIVE_TOKEN not in result.error, (
            f"sensitive token leaked into LlmResult.error: {result.error!r}"
        )
        # Assert — exact bus-bound form: only the exception class name, never str(exc).
        # Mock raises base nats.errors.Error directly, so type(exc).__name__ == "Error".
        assert result.error == "NATS transport error: Error"
        # Baseline guards
        assert result.ok is False
        assert result.retryable is True


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
        # Wire TurnStore so cli_sid resolves and the NATS path is reached
        store = _make_turn_store()
        store.get_cli_session = AsyncMock(return_value="cli-sid-abc")
        driver.set_turn_store(store)
        driver.link_lyra_session("pool-1", "sess-abc")

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
        """Returns False when ack['resumed'] is False (worker says not resumed)."""
        # Arrange
        driver = _make_driver()
        # Wire TurnStore so cli_sid resolves and the NATS path is reached
        store = _make_turn_store()
        store.get_cli_session = AsyncMock(return_value="cli-sid-abc")
        driver.set_turn_store(store)
        driver.link_lyra_session("pool-1", "sess-abc")

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
        before building the payload.
        """
        # Arrange
        driver = _make_driver()
        cli_sid = "cccccccc-dddd-dddd-dddd-dddddddddddd"
        store = _make_turn_store()
        store.get_cli_session = AsyncMock(return_value=cli_sid)
        driver.set_turn_store(store)
        driver.link_lyra_session("pool-1", "sess-xyz")
        captured: list[tuple[str, dict]] = []

        async def _mock_request(
            subject: str, payload_dict: dict, *, timeout: float | None = None
        ) -> dict:
            captured.append((subject, payload_dict))
            return {"ok": True, "resumed": True}

        # Act
        with patch.object(driver, "_request", new=_mock_request):
            await driver.resume_and_reset("pool-1", "sess-xyz")

        # Assert
        subject, payload = captured[0]
        assert subject == CliNatsDriver.SUBJECT_CONTROL
        assert payload.get("op") == "resume_and_reset"
        assert payload.get("pool_id") == "pool-1"
        # session_id is the TurnStore-resolved CLI session ID
        assert payload.get("session_id") == cli_sid

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

    @pytest.mark.asyncio
    async def test_resume_and_reset_turn_store_returns_none_returns_false(
        self,
    ) -> None:
        """TurnStore wired but returns None: return False, no NATS call."""
        # Arrange
        nc = _make_nc()
        driver = _make_driver(nc)
        store = _make_turn_store()
        store.get_cli_session = AsyncMock(return_value=None)
        driver.set_turn_store(store)
        driver.link_lyra_session("pool-1", "sess-1")

        # Act
        result = await driver.resume_and_reset("pool-1", "sess-1")

        # Assert — no NATS round-trip when cli_sid is None
        assert result is False
        nc.request.assert_not_called()


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

    @pytest.mark.asyncio
    async def test_complete_set_cli_session_exception_does_not_propagate(
        self,
    ) -> None:
        """set_cli_session failure must not propagate to the complete() caller."""

        # Arrange
        driver = _make_driver()
        store = _make_turn_store()
        store.set_cli_session = AsyncMock(side_effect=Exception("db down"))
        driver.set_turn_store(store)
        driver.link_lyra_session("pool-1", "lyra-sess-1")
        driver._nc.request = AsyncMock(
            return_value=_make_reply(
                {"result": "ok", "session_id": "cli-sid-123", "is_error": False}
            )
        )

        # Act
        result = await driver.complete("pool-1", "hi", _make_model_cfg(), "")
        await asyncio.sleep(0)  # let the fire-and-forget task run

        # Assert — complete() still returns ok=True despite store failure
        assert result.ok is True


class TestStreamGenLlmSessionPersistence:
    """_stream_gen_llm() persists cli_session_id when result chunk has session_id."""

    @pytest.mark.asyncio
    async def test_stream_gen_llm_calls_set_cli_session_on_result_chunk(self) -> None:
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

        async def _mock_dict_stream_gen(
            subject: str, payload_dict: dict, *, timeout: float | None = None
        ) -> AsyncIterator[dict]:
            for c in chunks:
                yield c

        # Act
        with patch.object(driver, "_dict_stream_gen", new=_mock_dict_stream_gen):
            async for ev in await driver.stream(
                "pool-1", "hello", _make_model_cfg(), "sys"
            ):
                events.append(ev)

        await asyncio.sleep(0)

        # Assert — ResultLlmEvent carries session_id
        result_events = [e for e in events if isinstance(e, ResultLlmEvent)]
        assert len(result_events) == 1
        assert result_events[0].session_id == cli_sid

        # Assert — set_cli_session persisted the mapping
        store.set_cli_session.assert_awaited_once_with(lyra_uuid, cli_sid)

    @pytest.mark.asyncio
    async def test_stream_gen_llm_no_set_cli_session_when_turn_store_none(self) -> None:
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

        async def _mock_dict_stream_gen(
            subject: str, payload_dict: dict, *, timeout: float | None = None
        ) -> AsyncIterator[dict]:
            for c in chunks:
                yield c

        # Act / Assert — no AttributeError
        with patch.object(driver, "_dict_stream_gen", new=_mock_dict_stream_gen):
            async for _ in await driver.stream(
                "pool-1", "hi", _make_model_cfg(), "sys"
            ):
                pass

    @pytest.mark.asyncio
    async def test_stream_gen_llm_set_cli_session_exception_does_not_propagate(
        self,
    ) -> None:
        """set_cli_session failure must not propagate to the streaming caller."""

        # Arrange
        driver = _make_driver()
        store = _make_turn_store()
        store.set_cli_session = AsyncMock(side_effect=Exception("db down"))
        driver.set_turn_store(store)
        driver.link_lyra_session("pool-1", "lyra-sess-1")

        chunks = [
            {"event_type": "text", "text": "hello", "done": False},
            {
                "event_type": "result",
                "session_id": "cli-sid-1",
                "is_error": False,
                "duration_ms": 10,
                "done": True,
            },
        ]

        async def _mock_dict_stream_gen(
            subject: str, payload_dict: dict, *, timeout: float | None = None
        ) -> AsyncIterator[dict]:
            for c in chunks:
                yield c

        # Act
        events = []
        with patch.object(driver, "_dict_stream_gen", new=_mock_dict_stream_gen):
            async for ev in await driver.stream("pool-1", "hi", _make_model_cfg(), ""):
                events.append(ev)
        await asyncio.sleep(0)  # let the fire-and-forget task run

        # Assert — streaming still completes, no exception propagated
        assert any(isinstance(ev, ResultLlmEvent) for ev in events)


# ---------------------------------------------------------------------------
# _fire_set_cli_session() done-callback (issue #1021)
# ---------------------------------------------------------------------------


class TestFireSetCliSessionCallback:
    """_fire_set_cli_session() logs exceptions via done-callback."""

    @pytest.mark.asyncio
    async def test_exception_produces_log_error(self) -> None:
        """set_cli_session failure triggers log.error via the done-callback."""
        # Arrange
        driver = _make_driver()
        store = _make_turn_store()
        store.set_cli_session = AsyncMock(side_effect=Exception("db down"))
        driver.set_turn_store(store)

        # Act — two yields: (1) run the task coroutine, (2) fire done-callback.
        # Both sleeps stay inside the patch scope so the mock is active when
        # _log_task_exc runs (CPython fires done-callbacks synchronously at task
        # completion, so two yields cover: schedule → run → callback).
        with patch("lyra.llm.drivers.cli_nats.log") as mock_log:
            driver._fire_set_cli_session("lyra-sess-1", "cli-sid-abc")
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            # Assert inside patch: mock active when callback fires
            mock_log.error.assert_called_once()
            assert "cli_nats" in mock_log.error.call_args[0][0]

    @pytest.mark.asyncio
    async def test_no_exception_does_not_log_error(self) -> None:
        """Successful set_cli_session does not trigger log.error."""
        # Arrange
        driver = _make_driver()
        store = _make_turn_store()
        store.set_cli_session = AsyncMock(return_value=None)
        driver.set_turn_store(store)

        # Act — assert inside patch so any spurious log.error during task
        # execution is captured by the mock (not the real logger)
        with patch("lyra.llm.drivers.cli_nats.log") as mock_log:
            driver._fire_set_cli_session("lyra-sess-1", "cli-sid-ok")
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            # Assert
            mock_log.error.assert_not_called()


# ---------------------------------------------------------------------------
# _log_task_exc unit tests — regression guard for the callback itself
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _build_cmd_payload — agent identity stamping from TraceContext (#1150)
# ---------------------------------------------------------------------------


class TestBuildCmdPayloadIdentity:
    """_build_cmd_payload() stamps agent identity from TraceContext (#1150).

    Today (trailers-only mode): agent_email is always None because AgentRow
    does not yet carry an email field.  agent_name is sourced from
    TraceContext.get_agent_name() when set.
    """

    def test_agent_name_from_trace_context_stamped_on_payload(self) -> None:
        """With TraceContext.set_agent_name active, payload carries agent_name.

        Parsing the resulting dict back through CliCmdPayload confirms
        agent_name == the trace-context value and agent_email is None
        (trailers-only mode).
        """
        from lyra.core.trace import TraceContext
        from roxabi_contracts.cli.models import CliCmdPayload

        # Arrange
        driver = _make_driver()
        driver.link_lyra_session("pool-1", "sess-abc")
        token = TraceContext.set_agent_name("agent-X")
        try:
            # Act
            payload_dict = driver._build_cmd_payload(
                "pool-1", "hello", _make_model_cfg(), "sys", stream=True
            )
        finally:
            TraceContext.reset_agent_name(token)

        # Assert — parse through the model to confirm schema validity
        envelope = CliCmdPayload.model_validate(payload_dict)
        assert envelope.agent_name == "agent-X"
        assert envelope.agent_email is None

    def test_no_trace_context_agent_name_yields_none(self) -> None:
        """Without a TraceContext agent_name set, payload has agent_name=None.

        Covers the fallback path where hub dispatches without an agent in
        the trace context (e.g. older code paths or unbound bots).
        """
        from lyra.core.trace import TraceContext
        from roxabi_contracts.cli.models import CliCmdPayload

        # Arrange — ensure no agent_name is in context
        driver = _make_driver()
        driver.link_lyra_session("pool-1", "sess-xyz")
        # Force the context var to empty-string (which production code maps to
        # None via `or None`). ContextVar.reset(token) restores the pre-set
        # value, which could be a stale "agent-X" from a prior test rather
        # than "unset" — so we set without resetting, and verify the
        # invariant before the Act step instead of trusting test isolation.
        TraceContext.set_agent_name("")
        assert TraceContext.get_agent_name() in ("", None)

        # Act — no set_agent_name call to a non-empty value
        payload_dict = driver._build_cmd_payload(
            "pool-1", "hello", _make_model_cfg(), "sys", stream=False
        )

        # Assert
        envelope = CliCmdPayload.model_validate(payload_dict)
        assert envelope.agent_name is None
        assert envelope.agent_email is None

    def test_lyra_session_id_still_present_on_payload(self) -> None:
        """Regression guard: lyra_session_id is carried on the envelope (#1008).

        Adding identity fields must not accidentally drop the session-id field.
        """
        from roxabi_contracts.cli.models import CliCmdPayload

        # Arrange
        driver = _make_driver()
        lyra_uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        driver.link_lyra_session("pool-1", lyra_uuid)

        # Act
        payload_dict = driver._build_cmd_payload(
            "pool-1", "hello", _make_model_cfg(), "sys", stream=True
        )

        # Assert
        envelope = CliCmdPayload.model_validate(payload_dict)
        assert envelope.lyra_session_id == lyra_uuid


# ---------------------------------------------------------------------------
# TestLogTaskExc unit tests — regression guard for the callback itself
# ---------------------------------------------------------------------------


class TestLogTaskExc:
    """Unit tests for _log_task_exc done-callback."""

    def test_calls_log_error_on_task_exception(self) -> None:
        """_log_task_exc calls log.error with exc_info when task raised."""
        from lyra.llm.drivers.cli_nats import _log_task_exc

        exc = Exception("db down")
        task = MagicMock()
        task.cancelled.return_value = False
        task.exception.return_value = exc
        task.get_name.return_value = "set_cli_session:abcdef12"

        with patch("lyra.llm.drivers.cli_nats.log") as mock_log:
            _log_task_exc(task)

        mock_log.error.assert_called_once()
        _, kwargs = mock_log.error.call_args
        assert kwargs.get("exc_info") is exc

    def test_no_log_when_task_succeeded(self) -> None:
        """_log_task_exc does not log when the task completed without exception."""
        from lyra.llm.drivers.cli_nats import _log_task_exc

        task = MagicMock()
        task.cancelled.return_value = False
        task.exception.return_value = None

        with patch("lyra.llm.drivers.cli_nats.log") as mock_log:
            _log_task_exc(task)

        mock_log.error.assert_not_called()

    def test_no_log_when_task_cancelled(self) -> None:
        """_log_task_exc does not log when the task was cancelled."""
        from lyra.llm.drivers.cli_nats import _log_task_exc

        task = MagicMock()
        task.cancelled.return_value = True

        with patch("lyra.llm.drivers.cli_nats.log") as mock_log:
            _log_task_exc(task)

        mock_log.error.assert_not_called()
