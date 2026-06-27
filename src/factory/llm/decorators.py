"""LlmProvider decorators: retry and circuit-breaker."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from factory.core.agent.agent_config import ModelConfig
from factory.core.lifecycle.circuit_breaker import CircuitBreaker
from factory.core.messaging.events import LlmEvent, ResultLlmEvent
from factory.llm.base import LlmResult, StreamingLlmProvider

log = logging.getLogger(__name__)


class RetryDecorator:
    """Retry failed LlmProvider calls with exponential backoff.

    Retries up to max_retries times after the initial attempt.
    Delay between attempt k and k+1: backoff_base * 2^k (k=0-based retry index).
    Returns immediately on success.
    """

    def __init__(
        self,
        inner: StreamingLlmProvider,
        max_retries: int = 3,
        backoff_base: float = 1.0,
    ) -> None:
        self._inner = inner
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self.capabilities: dict = inner.capabilities

    async def complete(  # noqa: PLR0913 — DEBT:wiring-bootstrap-deps
        self,
        pool_id: str,
        text: str,
        model_cfg: ModelConfig,
        system_prompt: str,
        *,
        messages: list[dict] | None = None,
    ) -> LlmResult:
        total_attempts = self._max_retries + 1
        result: LlmResult | None = None
        for attempt in range(total_attempts):
            result = await self._inner.complete(
                pool_id,
                text,
                model_cfg,
                system_prompt,
                messages=messages,
            )
            if result.ok:
                return result
            if not result.retryable:
                log.error(
                    "LlmProvider error (attempt %d/%d): %s — non-retryable, aborting",
                    attempt + 1,
                    total_attempts,
                    result.error,
                )
                return result
            if attempt < self._max_retries:
                delay = self._backoff_base * (2**attempt)
                log.warning(
                    "LlmProvider error (attempt %d/%d): %s — retrying in %.1fs",
                    attempt + 1,
                    total_attempts,
                    result.error,
                    delay,
                )
                await asyncio.sleep(delay)
        log.warning(
            "LlmProvider: all %d attempts failed: %s",
            total_attempts,
            result.error if result else "unknown",
        )
        assert result is not None  # total_attempts ≥ 1
        return result

    async def stream(  # noqa: PLR0913, C901 — DEBT:wiring-bootstrap-deps
        self,
        pool_id: str,
        text: str,
        model_cfg: ModelConfig,
        system_prompt: str,
        *,
        messages: list[dict] | None = None,
    ) -> AsyncIterator[LlmEvent]:
        """Retry connect-time and pre-first-event failures with exponential backoff.

        Retries when the inner generator raises before yielding its first
        non-terminal event, OR when it yields a terminal ResultLlmEvent(is_error=True)
        before any non-terminal event (connect-time failure). Once any non-terminal
        event (TextLlmEvent, ThinkingLlmEvent, ToolUse*, ToolResult) has been
        forwarded, never retry — the stream is live data and cannot be replayed.
        Cancellation (GeneratorExit / CancelledError) is never retried.
        On retry exhaustion with a terminal error event → forward the event so
        the consumer sees the failure. On exhaustion with an exception → re-raise.
        """
        total_attempts = self._max_retries + 1
        for attempt in range(total_attempts):
            first_non_terminal_seen = False
            retry_after_error_event = False
            try:
                async for event in self._inner.stream(
                    pool_id, text, model_cfg, system_prompt, messages=messages
                ):
                    if (
                        isinstance(event, ResultLlmEvent)
                        and event.is_error
                        and not first_non_terminal_seen
                    ):
                        # terminal error before any non-terminal event → retryable
                        if attempt < self._max_retries:
                            retry_after_error_event = True
                            break  # discard this terminal error; retry a fresh stream
                        yield event  # retries exhausted → surface the failure
                        return
                    if not isinstance(event, ResultLlmEvent):
                        first_non_terminal_seen = True
                    yield event
            except (GeneratorExit, asyncio.CancelledError):
                raise  # never retry cancellation
            except (TimeoutError, asyncio.TimeoutError, OSError, RuntimeError):
                if first_non_terminal_seen or attempt >= self._max_retries:
                    raise  # mid-stream (can't replay) or retries exhausted
            else:
                if not retry_after_error_event:
                    return  # stream finished normally (success terminal or clean end)
            delay = self._backoff_base * (2**attempt)
            log.warning(
                "stream connect error (attempt %d/%d) — retrying in %.1fs",
                attempt + 1,
                total_attempts,
                delay,
            )
            await asyncio.sleep(delay)

    def is_alive(self, pool_id: str) -> bool:
        return self._inner.is_alive(pool_id)


class CircuitBreakerDecorator:
    """Circuit-breaker guard around an LlmProvider.

    CB is OUTER, RetryDecorator is INNER.
    - Open circuit → return LlmResult(error=...) without calling inner.
    - record_success() on ok; record_failure() on error.
    - record_success() is a no-op in CLOSED state — intentional, not an error.
    """

    def __init__(self, inner: StreamingLlmProvider, cb: CircuitBreaker) -> None:
        self._inner = inner
        self._cb = cb
        self.capabilities: dict = inner.capabilities

    async def complete(  # noqa: PLR0913 — DEBT:wiring-bootstrap-deps
        self,
        pool_id: str,
        text: str,
        model_cfg: ModelConfig,
        system_prompt: str,
        *,
        messages: list[dict] | None = None,
    ) -> LlmResult:
        if self._cb.is_open():
            status = self._cb.get_status()
            retry_after = status.retry_after or 0.0
            msg = f"Circuit '{self._cb.name}' is open. Retry in {retry_after:.0f}s."
            return LlmResult(
                error=msg,
                retryable=False,
                user_message="Service temporarily unavailable. Please try again later.",
            )
        result = await self._inner.complete(
            pool_id,
            text,
            model_cfg,
            system_prompt,
            messages=messages,
        )
        if result.ok:
            self._cb.record_success()  # no-op when CLOSED; intentional
        else:
            self._cb.record_failure()
        return result

    async def stream(  # noqa: PLR0913 — DEBT:wiring-bootstrap-deps
        self,
        pool_id: str,
        text: str,
        model_cfg: ModelConfig,
        system_prompt: str,
        *,
        messages: list[dict] | None = None,
    ) -> AsyncIterator[LlmEvent]:
        """Guard streaming behind the circuit breaker.

        Open circuit → yields exactly one ResultLlmEvent(is_error=True) without
        calling inner. Closed/half-open → forwards inner events; records
        success/failure on the terminal ResultLlmEvent. Cancellation
        (GeneratorExit / CancelledError) releases the probe slot instead of
        recording a failure so the half-open probe flag is not left stuck.
        """
        if self._cb.is_open():
            status = self._cb.get_status()
            retry_after = status.retry_after or 0.0
            msg = f"Circuit '{self._cb.name}' is open. Retry in {retry_after:.0f}s."
            yield ResultLlmEvent(
                is_error=True, duration_ms=0, error_text=msg, worker_error=None
            )
            return

        outcome_recorded = False
        try:
            async for event in self._inner.stream(
                pool_id, text, model_cfg, system_prompt, messages=messages
            ):
                if isinstance(event, ResultLlmEvent) and not outcome_recorded:
                    if event.is_error:
                        self._cb.record_failure()
                    else:
                        self._cb.record_success()  # no-op when CLOSED; intentional
                    outcome_recorded = True
                yield event
            if not outcome_recorded:
                self._cb.record_failure()  # stream ended with no terminal event
        except (GeneratorExit, asyncio.CancelledError):
            if not outcome_recorded:
                self._cb.release_probe()  # cancelled mid-probe: free slot, no failure
            raise
        except (TimeoutError, asyncio.TimeoutError, OSError, RuntimeError):
            if not outcome_recorded:
                self._cb.record_failure()
            raise

    def is_alive(self, pool_id: str) -> bool:
        return self._inner.is_alive(pool_id)
