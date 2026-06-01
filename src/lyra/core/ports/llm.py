"""LlmProvider — Domain port for LLM completions.

Moved here from lyra.llm.base (V7 of hexagonal remediation, ADR-059).
lyra.llm.base re-exports for backward compatibility.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from lyra.core.agent.agent_config import ModelConfig
from lyra.core.messaging.events import LlmEvent

if TYPE_CHECKING:
    from roxabi_contracts.errors import WorkerError


@dataclass
class LlmResult:
    """Result returned by an LlmProvider.complete() call.

    Set ``retryable=False`` for errors that must not be retried
    (e.g. open circuit, invalid credentials, quota exhausted).
    Defaults to True so transient failures are retried automatically.

    ``worker_error`` carries the structured error envelope
    (ADR-066 (absorbed into ADR-049) / #1016).
    When populated, ``error`` is also set for backward compatibility (P2 shim).
    """

    result: str = ""
    session_id: str = ""
    error: str = ""
    retryable: bool = True
    warning: str = ""
    user_message: str = ""
    worker_error: "WorkerError | None" = field(default=None)

    @property
    def ok(self) -> bool:
        return not self.error


@runtime_checkable
class LlmProvider(Protocol):
    capabilities: dict[str, Any]

    async def complete(  # noqa: PLR0913 — DEBT:wiring-bootstrap-deps
        self,
        pool_id: str,
        text: str,
        model_cfg: ModelConfig,
        system_prompt: str,
        *,
        messages: list[dict] | None = None,
    ) -> LlmResult: ...

    def is_alive(self, pool_id: str) -> bool: ...

    # stream() is an optional duck-typed method — providers that support
    # streaming implement it as an async-generator function (`async def` with
    # `yield`) returning AsyncIterator[LlmEvent]. Callers iterate directly:
    # `async for event in provider.stream(...)` — no `await` needed.
    # SimpleAgent checks via hasattr() rather than isinstance() so that
    # existing providers are not broken by missing this method.
    def stream(
        self,
        pool_id: str,
        text: str,
        model_cfg: ModelConfig,
        system_prompt: str,
        *,
        messages: list[dict] | None = None,
    ) -> AsyncIterator[LlmEvent]: ...


class LlmUnavailableError(Exception):
    """Raised when no LLM worker is reachable (timeout, no heartbeat, circuit open)."""


__all__ = ["LlmProvider", "LlmResult", "LlmUnavailableError"]
