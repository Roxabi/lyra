from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

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
    capabilities: dict

    async def complete(
        self,
        pool_id: str,
        text: str,
        model_cfg: ModelConfig,
        system_prompt: str,
        *,
        messages: list[dict] | None = None,
    ) -> LlmResult: ...
