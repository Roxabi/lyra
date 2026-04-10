"""LlmProvider decorators: retry and circuit-breaker."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from lyra.core.agent_config import ModelConfig
from lyra.core.circuit_breaker import CircuitBreaker
from lyra.llm.base import LlmProvider, LlmResult
from lyra.llm.events import LlmEvent

log = logging.getLogger(__name__)


class RetryDecorator:
    """Retry failed LlmProvider calls with exponential backoff.

    Retries up to max_retries times after the initial attempt.
    Delay between attempt k and k+1: backoff_base * 2^k (k=0-based retry index).
    Returns immediately on success.
    """

    def __init__(
        self,
        inner: LlmProvider,
        max_retries: int = 3,
        backoff_base: float = 1.0,
    ) -> None:
        self._inner = inner
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self.capabilities: dict = inner.capabilities

    async def complete(  # noqa: PLR0913
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

    async def stream(  # noqa: PLR0913
        self,
        pool_id: str,
        text: str,
        model_cfg: ModelConfig,
        system_prompt: str,
        *,
        messages: list[dict] | None = None,
    ) -> AsyncIterator[LlmEvent]:
        """Delegate streaming to inner provider (no retry — stream is live data)."""
        return await self._inner.stream(
            pool_id, text, model_cfg, system_prompt, messages=messages
        )

    def is_alive(self, pool_id: str) -> bool:
        return self._inner.is_alive(pool_id)


class CircuitBreakerDecorator:
    """Circuit-breaker guard around an LlmProvider.

    CB is OUTER, RetryDecorator is INNER.
    - Open circuit → return LlmResult(error=...) without calling inner.
    - record_success() on ok; record_failure() on error.
    - record_success() is a no-op in CLOSED state — intentional, not an error.
    """

    def __init__(self, inner: LlmProvider, cb: CircuitBreaker) -> None:
        self._inner = inner
        self._cb = cb
        self.capabilities: dict = inner.capabilities

    async def complete(  # noqa: PLR0913
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

    async def stream(  # noqa: PLR0913
        self,
        pool_id: str,
        text: str,
        model_cfg: ModelConfig,
        system_prompt: str,
        *,
        messages: list[dict] | None = None,
    ) -> AsyncIterator[LlmEvent]:
        """Delegate to inner provider (circuit check applies to complete() only)."""
        return await self._inner.stream(
            pool_id, text, model_cfg, system_prompt, messages=messages
        )

    def is_alive(self, pool_id: str) -> bool:
        return self._inner.is_alive(pool_id)
