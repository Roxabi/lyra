"""Tests for RetryDecorator and CircuitBreakerDecorator.

RED phase — these tests will fail until S4 implementation lands.
Source: src/factory/llm/decorators.py
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from factory.core.agent.agent_config import ModelConfig
from factory.core.lifecycle.circuit_breaker import (
    CircuitBreaker,
    CircuitState,
    CircuitStatus,
)
from factory.core.messaging.events import LlmEvent, ResultLlmEvent, TextLlmEvent
from factory.llm.base import LlmResult
from factory.llm.decorators import (
    CircuitBreakerDecorator,
    RetryDecorator,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_model_cfg() -> ModelConfig:
    return ModelConfig()


def make_ok_result(text: str = "ok") -> LlmResult:
    return LlmResult(result=text)


def make_error_result(msg: str = "boom") -> LlmResult:
    return LlmResult(error=msg)


def _make_inner(return_values: list[LlmResult]) -> MagicMock:
    """Return a mock LlmProvider whose complete() returns values in sequence."""
    inner = MagicMock()
    inner.complete = AsyncMock(side_effect=return_values)
    inner.capabilities = {"streaming": False, "auth": "api_key"}
    return inner


async def _complete(
    driver: RetryDecorator | CircuitBreakerDecorator,
) -> LlmResult:
    return await driver.complete(
        pool_id="p1",
        text="hi",
        model_cfg=make_model_cfg(),
        system_prompt="",
    )


# ---------------------------------------------------------------------------
# LlmResult
# ---------------------------------------------------------------------------


class TestLlmResult:
    def test_retryable_defaults_to_true(self) -> None:
        """LlmResult.retryable is True by default (transient-error contract)."""
        assert LlmResult().retryable is True
        assert LlmResult(error="any error").retryable is True

    def test_retryable_can_be_set_false(self) -> None:
        """LlmResult.retryable=False is preserved (permanent-error contract)."""
        result = LlmResult(error="fatal", retryable=False)
        assert result.retryable is False


# ---------------------------------------------------------------------------
# RetryDecorator
# ---------------------------------------------------------------------------


class TestRetryDecorator:
    async def test_retry_returns_on_first_success(self) -> None:
        """Inner returns ok=True on first call → inner called exactly once."""
        # Arrange
        inner = _make_inner([make_ok_result()])
        decorator = RetryDecorator(inner, max_retries=3, backoff_base=0.0)

        # Act
        result = await _complete(decorator)

        # Assert
        assert result.ok is True
        assert inner.complete.call_count == 1

    async def test_retry_retries_on_error(self) -> None:
        """Inner always errors → 1 initial + max_retries calls; final error returned."""
        # Arrange
        errors = [make_error_result(f"err-{i}") for i in range(4)]
        inner = _make_inner(errors)
        decorator = RetryDecorator(inner, max_retries=3, backoff_base=0.0)

        # Act
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await _complete(decorator)

        # Assert — 1 initial + 3 retries = 4 total calls
        assert result.ok is False
        assert inner.complete.call_count == 4

    async def test_retry_stops_early_on_success(self) -> None:
        """Inner: [error, ok] → called twice; ok result returned."""
        # Arrange
        inner = _make_inner([make_error_result(), make_ok_result("second try")])
        decorator = RetryDecorator(inner, max_retries=3, backoff_base=0.0)

        # Act
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await _complete(decorator)

        # Assert
        assert result.ok is True
        assert result.result == "second try"
        assert inner.complete.call_count == 2

    async def test_retry_stops_on_non_retryable_error(self) -> None:
        """retryable=False → inner called exactly once; no sleep; error returned."""
        # Arrange
        non_retryable = LlmResult(error="circuit open", retryable=False)
        inner = _make_inner([non_retryable])
        decorator = RetryDecorator(inner, max_retries=3, backoff_base=0.0)

        # Act
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await _complete(decorator)

        # Assert
        assert result.ok is False
        assert result.retryable is False
        assert result.error == "circuit open"
        assert inner.complete.call_count == 1
        mock_sleep.assert_not_called()

    async def test_retry_stops_on_non_retryable_error_mid_sequence(self) -> None:
        """retryable=False on attempt 1 → inner called twice; sleep once; aborts."""
        # Arrange — first result is retryable, second is not
        inner = _make_inner(
            [
                make_error_result("transient"),
                LlmResult(error="fatal", retryable=False),
            ]
        )
        decorator = RetryDecorator(inner, max_retries=3, backoff_base=0.0)

        # Act
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await _complete(decorator)

        # Assert — retried once, then aborted on non-retryable result
        assert result.ok is False
        assert result.retryable is False
        assert result.error == "fatal"
        assert inner.complete.call_count == 2
        assert mock_sleep.call_count == 1

    async def test_retry_exponential_backoff(self) -> None:
        """Sleep called with base * 2^k between retry attempts (base=1.0)."""
        # Arrange — 3 errors so 3 sleeps happen (after attempt 0, 1, 2)
        errors = [make_error_result() for _ in range(4)]
        inner = _make_inner(errors)
        decorator = RetryDecorator(inner, max_retries=3, backoff_base=1.0)

        # Act
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await _complete(decorator)

        # Assert — sleep called with 1.0, 2.0, 4.0 (base * 2^0, 2^1, 2^2)
        assert mock_sleep.call_count == 3
        sleep_args = [call.args[0] for call in mock_sleep.call_args_list]
        assert sleep_args[0] == pytest.approx(1.0)
        assert sleep_args[1] == pytest.approx(2.0)
        assert sleep_args[2] == pytest.approx(4.0)

    # -----------------------------------------------------------------------
    # stream() tests — RED phase (#1819)
    # -----------------------------------------------------------------------

    async def test_retry_stream_retries_before_first_event(self) -> None:
        """Inner stream raises before first event → retry; succeeds on second."""
        # Arrange
        attempt = 0

        async def _agen_fail_then_succeed():  # type: ignore[no-untyped-def]
            nonlocal attempt
            attempt += 1
            if attempt == 1:
                raise ConnectionError("transient failure")
            yield TextLlmEvent(text="hello")
            yield ResultLlmEvent(is_error=False, duration_ms=5)

        inner = MagicMock()
        inner.stream = lambda *a, **k: _agen_fail_then_succeed()
        inner.capabilities = {"streaming": True, "auth": "api_key"}

        decorator = RetryDecorator(inner, max_retries=3, backoff_base=0.1)

        # Act
        events: list = []
        with patch("factory.llm.decorators.asyncio.sleep", new_callable=AsyncMock):
            async for event in decorator.stream(
                pool_id="p1",
                text="hi",
                model_cfg=make_model_cfg(),
                system_prompt="",
            ):
                events.append(event)

        # Assert — inner was called twice; events from second attempt forwarded
        assert attempt == 2
        assert len(events) == 2
        assert isinstance(events[0], TextLlmEvent)
        assert isinstance(events[1], ResultLlmEvent)
        assert events[1].is_error is False

    async def test_retry_stream_no_retry_after_first_event(self) -> None:
        """Inner stream yields one event then raises → no retry; propagates."""
        # Arrange
        attempt = 0

        async def _agen_fail_mid():  # type: ignore[no-untyped-def]
            nonlocal attempt
            attempt += 1
            yield TextLlmEvent(text="partial")
            raise RuntimeError("mid-stream failure")

        inner = MagicMock()
        inner.stream = lambda *a, **k: _agen_fail_mid()
        inner.capabilities = {"streaming": True, "auth": "api_key"}

        decorator = RetryDecorator(inner, max_retries=3, backoff_base=0.1)

        # Act + Assert — exception propagates; inner called only once (no retry)
        with patch(
            "factory.llm.decorators.asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep:
            with pytest.raises(RuntimeError, match="mid-stream failure"):
                async for _ in decorator.stream(
                    pool_id="p1",
                    text="hi",
                    model_cfg=make_model_cfg(),
                    system_prompt="",
                ):
                    pass

        assert attempt == 1
        mock_sleep.assert_not_called()

    async def test_retry_stream_forwards_all_events_in_order(self) -> None:
        """Events forwarded in exact order; no events dropped or reordered."""

        # Arrange
        async def _agen(events):  # type: ignore[no-untyped-def]
            for e in events:
                yield e

        expected = [
            TextLlmEvent(text="chunk-1"),
            TextLlmEvent(text="chunk-2"),
            TextLlmEvent(text="chunk-3"),
            ResultLlmEvent(is_error=False, duration_ms=15),
        ]

        inner = MagicMock()
        inner.stream = lambda *a, **k: _agen(list(expected))
        inner.capabilities = {"streaming": True, "auth": "api_key"}

        decorator = RetryDecorator(inner, max_retries=3, backoff_base=0.0)

        # Act
        events: list = []
        async for event in decorator.stream(
            pool_id="p1",
            text="hi",
            model_cfg=make_model_cfg(),
            system_prompt="",
        ):
            events.append(event)

        # Assert — order and identity preserved
        assert events == expected

    async def test_retry_stream_exhausts_retries_then_raises(self) -> None:
        """Inner ALWAYS raises ConnectionError before first event.

        max_retries=3 → 4 total attempts; ConnectionError propagates after last.
        Sleep called 3 times with delays [0.1, 0.2, 0.4].
        """
        # Arrange
        call_count = 0

        async def _agen_always_raises():  # type: ignore[no-untyped-def]
            nonlocal call_count
            call_count += 1
            raise ConnectionError("connect refused")
            yield  # pragma: no cover — unreachable; makes this an async generator

        inner = MagicMock()
        inner.stream = lambda *a, **k: _agen_always_raises()
        inner.capabilities = {"streaming": True, "auth": "api_key"}

        decorator = RetryDecorator(inner, max_retries=3, backoff_base=0.1)

        # Act + Assert
        with patch(
            "factory.llm.decorators.asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep:
            with pytest.raises(ConnectionError, match="connect refused"):
                async for _ in decorator.stream(
                    pool_id="p1",
                    text="hi",
                    model_cfg=make_model_cfg(),
                    system_prompt="",
                ):
                    pass

        assert call_count == 4  # 1 initial + 3 retries
        assert mock_sleep.call_count == 3
        sleep_args = [call.args[0] for call in mock_sleep.call_args_list]
        assert sleep_args[0] == pytest.approx(0.1)
        assert sleep_args[1] == pytest.approx(0.2)
        assert sleep_args[2] == pytest.approx(0.4)

    async def test_retry_stream_retries_on_terminal_error_event_before_first_event(
        self,
    ) -> None:
        """Attempt 1 yields terminal error before non-terminal → retry.

        Attempt 2 yields TextLlmEvent + success terminal.
        Consumer receives ONLY attempt-2 events (error event discarded).
        """
        # Arrange
        attempt = 0

        async def _agen_fail_then_succeed():  # type: ignore[no-untyped-def]
            nonlocal attempt
            attempt += 1
            if attempt == 1:
                yield ResultLlmEvent(is_error=True, duration_ms=1)
            else:
                yield TextLlmEvent(text="ok")
                yield ResultLlmEvent(is_error=False, duration_ms=5)

        inner = MagicMock()
        inner.stream = lambda *a, **k: _agen_fail_then_succeed()
        inner.capabilities = {"streaming": True, "auth": "api_key"}

        decorator = RetryDecorator(inner, max_retries=3, backoff_base=0.1)

        # Act
        events: list = []
        with patch("factory.llm.decorators.asyncio.sleep", new_callable=AsyncMock):
            async for event in decorator.stream(
                pool_id="p1",
                text="hi",
                model_cfg=make_model_cfg(),
                system_prompt="",
            ):
                events.append(event)

        # Assert — attempt 2 events only; error event from attempt 1 discarded
        assert attempt == 2
        assert len(events) == 2
        assert isinstance(events[0], TextLlmEvent)
        assert events[0].text == "ok"
        assert isinstance(events[1], ResultLlmEvent)
        assert events[1].is_error is False

    async def test_retry_stream_exhausts_then_forwards_terminal_error(self) -> None:
        """Inner ALWAYS yields ResultLlmEvent(is_error=True) before non-terminal.

        max_retries=2 → 3 total attempts; consumer receives exactly ONE
        terminal error event; sleep called 2 times.
        """
        # Arrange
        call_count = 0

        async def _agen_always_error():  # type: ignore[no-untyped-def]
            nonlocal call_count
            call_count += 1
            yield ResultLlmEvent(is_error=True, duration_ms=1)

        inner = MagicMock()
        inner.stream = lambda *a, **k: _agen_always_error()
        inner.capabilities = {"streaming": True, "auth": "api_key"}

        decorator = RetryDecorator(inner, max_retries=2, backoff_base=0.1)

        # Act
        events: list = []
        with patch(
            "factory.llm.decorators.asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep:
            async for event in decorator.stream(
                pool_id="p1",
                text="hi",
                model_cfg=make_model_cfg(),
                system_prompt="",
            ):
                events.append(event)

        # Assert — 3 total calls, 2 sleeps, exactly 1 terminal error event surfaced
        assert call_count == 3
        assert mock_sleep.call_count == 2
        assert len(events) == 1
        assert isinstance(events[0], ResultLlmEvent)
        assert events[0].is_error is True


# ---------------------------------------------------------------------------
# CircuitBreakerDecorator
# ---------------------------------------------------------------------------


class TestCircuitBreakerDecorator:
    async def test_cb_open_circuit_returns_error_without_calling_inner(
        self,
    ) -> None:
        """cb.is_open() == True → LlmResult(ok=False) returned; inner never called."""
        # Arrange
        cb = MagicMock(spec=CircuitBreaker)
        cb.name = "claude-cli"
        cb.is_open.return_value = True
        cb.get_status.return_value = MagicMock(retry_after=30.0)

        inner = _make_inner([make_ok_result()])
        decorator = CircuitBreakerDecorator(inner, cb)

        # Act
        result = await _complete(decorator)

        # Assert
        assert result.ok is False
        assert result.error != ""
        assert result.retryable is False
        inner.complete.assert_not_called()

    async def test_cb_records_success(self) -> None:
        """On ok result, cb.record_success() is called; record_failure is not."""
        # Arrange
        cb = MagicMock(spec=CircuitBreaker)
        cb.name = "claude-cli"
        cb.is_open.return_value = False

        inner = _make_inner([make_ok_result()])
        decorator = CircuitBreakerDecorator(inner, cb)

        # Act
        result = await _complete(decorator)

        # Assert
        assert result.ok is True
        cb.record_success.assert_called_once()
        cb.record_failure.assert_not_called()

    async def test_cb_records_failure(self) -> None:
        """On error result, cb.record_failure() is called; record_success is not."""
        # Arrange
        cb = MagicMock(spec=CircuitBreaker)
        cb.name = "claude-cli"
        cb.is_open.return_value = False

        inner = _make_inner([make_error_result("sdk down")])
        decorator = CircuitBreakerDecorator(inner, cb)

        # Act
        result = await _complete(decorator)

        # Assert
        assert result.ok is False
        cb.record_failure.assert_called_once()
        cb.record_success.assert_not_called()

    async def test_cb_record_success_noop_when_closed(self) -> None:
        """Real CB in CLOSED state: record_success() is a no-op, no exception."""
        # Arrange — real CircuitBreaker (not a mock) to verify no-op contract
        real_cb = CircuitBreaker(name="test", failure_threshold=5, recovery_timeout=60)
        # real_cb starts CLOSED

        inner = _make_inner([make_ok_result()])
        decorator = CircuitBreakerDecorator(inner, real_cb)

        # Act — should not raise
        result = await _complete(decorator)

        # Assert — result ok, circuit still closed, no exception
        assert result.ok is True
        assert real_cb.is_open() is False

    # -----------------------------------------------------------------------
    # stream() tests — RED phase (#1819)
    # -----------------------------------------------------------------------

    async def test_cb_stream_open_circuit_yields_error_without_calling_inner(
        self,
    ) -> None:
        """Open circuit: yields ONE ResultLlmEvent(is_error=True, worker_error=None).

        Inner never called."""
        # Arrange
        cb = MagicMock(spec=CircuitBreaker)
        cb.configure_mock(name="claude-cli")
        cb.is_open.return_value = True
        cb.get_status.return_value = CircuitStatus(
            name="claude-cli",
            state=CircuitState.OPEN,
            failure_count=5,
            retry_after=12.0,
        )

        inner_called = False

        async def _agen(events):  # type: ignore[no-untyped-def]
            nonlocal inner_called
            inner_called = True
            for e in events:
                yield e

        inner = MagicMock()
        inner.stream = lambda *a, **k: _agen([])
        inner.capabilities = {"streaming": True, "auth": "api_key"}

        decorator = CircuitBreakerDecorator(inner, cb)

        # Act
        events: list = []
        async for event in decorator.stream(
            pool_id="p1",
            text="hi",
            model_cfg=make_model_cfg(),
            system_prompt="",
        ):
            events.append(event)

        # Assert
        assert len(events) == 1
        evt = events[0]
        assert isinstance(evt, ResultLlmEvent)
        assert evt.is_error is True
        assert evt.worker_error is None
        assert inner_called is False

    async def test_cb_stream_records_success_on_clean_terminal(self) -> None:
        """Clean terminal (is_error=False): record_success once; record_failure not."""
        # Arrange
        cb = MagicMock(spec=CircuitBreaker)
        cb.configure_mock(name="claude-cli")
        cb.is_open.return_value = False

        async def _agen(events):  # type: ignore[no-untyped-def]
            for e in events:
                yield e

        inner = MagicMock()
        inner.stream = lambda *a, **k: _agen(
            [
                TextLlmEvent(text="hello"),
                ResultLlmEvent(is_error=False, duration_ms=10),
            ]
        )
        inner.capabilities = {"streaming": True, "auth": "api_key"}

        decorator = CircuitBreakerDecorator(inner, cb)

        # Act — consume fully
        async for _ in decorator.stream(
            pool_id="p1",
            text="hi",
            model_cfg=make_model_cfg(),
            system_prompt="",
        ):
            pass

        # Assert
        cb.record_success.assert_called_once()
        cb.record_failure.assert_not_called()

    async def test_cb_stream_records_failure_on_error_terminal(self) -> None:
        """Error terminal (is_error=True): record_failure once; record_success not."""
        # Arrange
        cb = MagicMock(spec=CircuitBreaker)
        cb.configure_mock(name="claude-cli")
        cb.is_open.return_value = False

        async def _agen(events):  # type: ignore[no-untyped-def]
            for e in events:
                yield e

        inner = MagicMock()
        inner.stream = lambda *a, **k: _agen(
            [ResultLlmEvent(is_error=True, duration_ms=5)]
        )
        inner.capabilities = {"streaming": True, "auth": "api_key"}

        decorator = CircuitBreakerDecorator(inner, cb)

        # Act — consume fully
        async for _ in decorator.stream(
            pool_id="p1",
            text="hi",
            model_cfg=make_model_cfg(),
            system_prompt="",
        ):
            pass

        # Assert
        cb.record_failure.assert_called_once()
        cb.record_success.assert_not_called()

    async def test_cb_stream_records_failure_on_exception(self) -> None:
        """Exception mid-stream: propagates AND record_failure called once."""
        # Arrange
        cb = MagicMock(spec=CircuitBreaker)
        cb.configure_mock(name="claude-cli")
        cb.is_open.return_value = False

        async def _agen_raises():  # type: ignore[no-untyped-def]
            yield TextLlmEvent(text="partial")
            raise RuntimeError("provider died")

        inner = MagicMock()
        inner.stream = lambda *a, **k: _agen_raises()
        inner.capabilities = {"streaming": True, "auth": "api_key"}

        decorator = CircuitBreakerDecorator(inner, cb)

        # Act + Assert exception propagates
        with pytest.raises(RuntimeError, match="provider died"):
            async for _ in decorator.stream(
                pool_id="p1",
                text="hi",
                model_cfg=make_model_cfg(),
                system_prompt="",
            ):
                pass

        cb.record_failure.assert_called_once()
        cb.record_success.assert_not_called()

    async def test_cb_stream_no_failure_on_cancel_releases_probe(self) -> None:
        """Consumer cancels (aclose): record_failure NOT called; release_probe once."""
        # Arrange
        cb = MagicMock(spec=CircuitBreaker)
        cb.configure_mock(name="claude-cli")
        cb.is_open.return_value = False

        async def _agen(events):  # type: ignore[no-untyped-def]
            for e in events:
                yield e

        inner = MagicMock()
        inner.stream = lambda *a, **k: _agen(
            [
                TextLlmEvent(text="first"),
                TextLlmEvent(text="second"),
                ResultLlmEvent(is_error=False, duration_ms=20),
            ]
        )
        inner.capabilities = {"streaming": True, "auth": "api_key"}

        decorator = CircuitBreakerDecorator(inner, cb)

        # Act — consume one event then cancel
        gen = cast(
            "AsyncGenerator[LlmEvent, None]",
            decorator.stream(
                pool_id="p1",
                text="hi",
                model_cfg=make_model_cfg(),
                system_prompt="",
            ),
        )
        await gen.__anext__()  # consume first event
        await gen.aclose()  # cancel mid-stream

        # Assert
        cb.record_failure.assert_not_called()
        cb.release_probe.assert_called_once()

    async def test_cb_stream_records_failure_on_no_terminal_event(self) -> None:
        """Stream ends with only TextLlmEvent(s) and no ResultLlmEvent (truncation).

        record_failure called once; record_success not called.
        """
        # Arrange
        cb = MagicMock(spec=CircuitBreaker)
        cb.configure_mock(name="claude-cli")
        cb.is_open.return_value = False

        async def _agen(events):  # type: ignore[no-untyped-def]
            for e in events:
                yield e

        inner = MagicMock()
        inner.stream = lambda *a, **k: _agen(
            [
                TextLlmEvent(text="partial-1"),
                TextLlmEvent(text="partial-2"),
                # deliberately no ResultLlmEvent — truncated stream
            ]
        )
        inner.capabilities = {"streaming": True, "auth": "api_key"}

        decorator = CircuitBreakerDecorator(inner, cb)

        # Act — consume fully
        async for _ in decorator.stream(
            pool_id="p1",
            text="hi",
            model_cfg=make_model_cfg(),
            system_prompt="",
        ):
            pass

        # Assert
        cb.record_failure.assert_called_once()
        cb.record_success.assert_not_called()

    async def test_cb_stream_cancel_via_task_cancel_releases_probe(self) -> None:
        """task.cancel() mid-stream: release_probe called; record_failure not called."""
        # Arrange
        cb = MagicMock(spec=CircuitBreaker)
        cb.configure_mock(name="claude-cli")
        cb.is_open.return_value = False

        first_event_reached = asyncio.Event()

        async def _agen_slow():  # type: ignore[no-untyped-def]
            yield TextLlmEvent(text="first")
            first_event_reached.set()
            await asyncio.sleep(10)  # blocked — will be cancelled
            yield ResultLlmEvent(is_error=False, duration_ms=10)

        inner = MagicMock()
        inner.stream = lambda *a, **k: _agen_slow()
        inner.capabilities = {"streaming": True, "auth": "api_key"}

        decorator = CircuitBreakerDecorator(inner, cb)

        async def _consume() -> None:
            async for _ in decorator.stream(
                pool_id="p1",
                text="hi",
                model_cfg=make_model_cfg(),
                system_prompt="",
            ):
                pass

        # Act — start task, wait until it has consumed first event, then cancel
        task = asyncio.create_task(_consume())
        await first_event_reached.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # Assert
        cb.release_probe.assert_called_once()
        cb.record_failure.assert_not_called()


# ---------------------------------------------------------------------------
# Integration: RetryDecorator(CircuitBreakerDecorator(driver))
# ---------------------------------------------------------------------------


class TestRetryCircuitBreakerIntegration:
    """Verify that Retry(CB(driver)) does not burn retries against an open circuit.

    This is the scenario identified in PR #170 review finding #8:
    if stacking is reversed (Retry outer, CB inner), the retryable=False flag
    on the open-circuit result must cause RetryDecorator to abort immediately.
    """

    async def test_retry_does_not_burn_attempts_against_open_circuit(self) -> None:
        """Retry(CB(driver)) — open circuit → driver never called, no sleep."""
        # Arrange — real CircuitBreaker forced open
        cb = MagicMock(spec=CircuitBreaker)
        cb.name = "openai"
        cb.is_open.return_value = True
        cb.get_status.return_value = MagicMock(retry_after=60.0)

        driver = _make_inner([make_ok_result()])
        cb_decorator = CircuitBreakerDecorator(driver, cb)
        retry_decorator = RetryDecorator(cb_decorator, max_retries=3, backoff_base=0.0)

        # Act
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await retry_decorator.complete(
                pool_id="p1",
                text="hi",
                model_cfg=make_model_cfg(),
                system_prompt="",
            )

        # Assert — non-retryable circuit-open error propagates; driver untouched
        assert result.ok is False
        assert result.retryable is False
        assert "openai" in result.error
        driver.complete.assert_not_called()
        mock_sleep.assert_not_called()
