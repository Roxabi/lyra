from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from lyra.core.agent_config import ModelConfig


@dataclass
class LlmResult:
    """Result returned by an LlmProvider.complete() call.

    Set ``retryable=False`` for errors that must not be retried
    (e.g. open circuit, invalid credentials, quota exhausted).
    Defaults to True so transient failures are retried automatically.
    """

    result: str = ""
    session_id: str = ""
    error: str = ""
    retryable: bool = True
    warning: str = ""
    user_message: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


@runtime_checkable
class LlmProvider(Protocol):
    capabilities: dict[str, Any]

    async def complete(  # noqa: PLR0913
        self,
        pool_id: str,
        text: str,
        model_cfg: ModelConfig,
        system_prompt: str,
        *,
        messages: list[dict] | None = None,
        on_intermediate: Callable[[str], Awaitable[None]] | None = None,
    ) -> LlmResult: ...

    def is_alive(self, pool_id: str) -> bool: ...

    # stream() is an optional duck-typed method — providers that support
    # streaming implement it; SimpleAgent checks via hasattr().
    # Not part of the protocol to avoid breaking existing providers.
