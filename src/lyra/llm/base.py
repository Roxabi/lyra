from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from lyra.core.agent import ModelConfig


@dataclass
class LlmResult:
    result: str = ""
    session_id: str = ""
    error: str = ""
    warning: str = ""

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
