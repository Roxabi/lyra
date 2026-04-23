"""Shared fixtures and helpers for tests/llm/."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from lyra.core.agent.agent_config import Complexity, ModelConfig, SmartRoutingConfig
from lyra.llm.base import LlmResult


def make_model_cfg(model: str = "claude-sonnet-4-6") -> ModelConfig:
    return ModelConfig(backend="claude-cli", model=model)


def make_ok_result(text: str = "ok") -> LlmResult:
    return LlmResult(result=text)


def _make_inner(return_values: list[LlmResult] | None = None) -> MagicMock:
    inner = MagicMock()
    inner.complete = (
        AsyncMock(return_value=make_ok_result())
        if return_values is None
        else MagicMock()
    )
    if return_values is not None:
        inner.complete = AsyncMock(side_effect=return_values)
    inner.capabilities = {"streaming": False, "auth": "api_key"}
    return inner


def _make_config(
    enabled: bool = True,
    routing_table: dict[Complexity, str] | None = None,
    history_size: int = 50,
) -> SmartRoutingConfig:
    table = routing_table or {
        Complexity.TRIVIAL: "claude-haiku-4-5-20251001",
        Complexity.SIMPLE: "claude-haiku-4-5-20251001",
        Complexity.MODERATE: "claude-sonnet-4-6",
        Complexity.COMPLEX: "claude-opus-4-6",
    }
    return SmartRoutingConfig(
        enabled=enabled, routing_table=table, history_size=history_size
    )
