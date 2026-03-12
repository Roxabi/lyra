"""LlmProvider decorators: retry and circuit-breaker."""
from __future__ import annotations

import asyncio
import logging

from lyra.core.agent import ModelConfig
from lyra.core.circuit_breaker import CircuitBreaker
from lyra.llm.base import LlmProvider, LlmResult

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

    async def complete(
        self,
        pool_id: str,
        text: str,
        model_cfg: ModelConfig,
        system_prompt: str,
        *,
        messages: list[dict] | None = None,
    ) -> LlmResult:
        result = await self._inner.complete(
            pool_id, text, model_cfg, system_prompt, messages=messages
        )
        for attempt in range(self._max_retries):
            if result.ok:
                return result
            delay = self._backoff_base * (2**attempt)
            log.warning(
                "LlmProvider error (attempt %d/%d): %s — retrying in %.1fs",
                attempt + 1,
                self._max_retries,
                result.error,
                delay,
            )
            await asyncio.sleep(delay)
            result = await self._inner.complete(
                pool_id, text, model_cfg, system_prompt, messages=messages
            )
        return result


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

    async def complete(
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
            return LlmResult(
                error=f"Circuit '{self._cb.name}' is open. Retry in {retry_after:.0f}s."
            )
        result = await self._inner.complete(
            pool_id, text, model_cfg, system_prompt, messages=messages
        )
        if result.ok:
            self._cb.record_success()  # no-op when CLOSED; intentional
        else:
            self._cb.record_failure()
        return result
