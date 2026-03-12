"""Tests for RetryDecorator and CircuitBreakerDecorator.

RED phase — these tests will fail until S4 implementation lands.
Source: src/lyra/llm/decorators.py
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lyra.core.agent import ModelConfig
from lyra.core.circuit_breaker import CircuitBreaker
from lyra.llm.base import LlmResult
from lyra.llm.decorators import (  # type: ignore[reportMissingImports]
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
        cb.name = "anthropic"
        cb.is_open.return_value = True
        cb.get_status.return_value = MagicMock(retry_after=30.0)

        inner = _make_inner([make_ok_result()])
        decorator = CircuitBreakerDecorator(inner, cb)

        # Act
        result = await _complete(decorator)

        # Assert
        assert result.ok is False
        assert result.error != ""
        inner.complete.assert_not_called()

    async def test_cb_records_success(self) -> None:
        """On ok result, cb.record_success() is called; record_failure is not."""
        # Arrange
        cb = MagicMock(spec=CircuitBreaker)
        cb.name = "anthropic"
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
        cb.name = "anthropic"
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
