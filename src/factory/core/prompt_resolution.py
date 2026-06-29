"""Turn-stage prompt resolution — single primitive for harness parity."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from factory.core.agent.agent_config import Agent
    from factory.core.pool import Pool

__all__ = ["resolve_effective_system_prompt", "resolve_agent_runtime_defaults"]


def resolve_effective_system_prompt(agent: "Agent", pool: "Pool") -> str:
    """Return the opaque system prompt for this turn (pool cache wins)."""
    if pool._system_prompt:
        return pool._system_prompt
    return agent.system_prompt or ""


def resolve_agent_runtime_defaults(
    *,
    backend: str,
    model: str,
) -> dict[str, str]:
    """Hub-resolved harness defaults for dashboard jobs and chat tabs."""
    return {"backend": backend, "model": model}