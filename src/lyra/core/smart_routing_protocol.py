"""Smart routing protocol and shared data types.

Lives in ``core/`` so that command handlers can depend on the routing decorator
abstractly without importing from ``llm/`` — preserving the unidirectional
``llm → core`` layering (enforced by ``import-linter``).

``llm.smart_routing.SmartRoutingDecorator`` structurally satisfies
``SmartRoutingProtocol``; no runtime inheritance is required.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from lyra.core.agent.agent_config import Complexity


@dataclass(frozen=True)
class RoutingDecision:
    """Record of a single smart-routing decision."""

    complexity: Complexity
    original_model: str
    routed_model: str
    reason: str
    timestamp: float
    message_preview: str


@runtime_checkable
class SmartRoutingProtocol(Protocol):
    """Structural type for the smart-routing decorator as seen from ``core/``.

    ``history`` is typed as ``Sequence`` (not ``deque``) so callers can only
    iterate / index — they must not rely on append/pop semantics of the
    concrete container.
    """

    @property
    def history(self) -> Sequence[RoutingDecision]: ...


__all__ = ["RoutingDecision", "SmartRoutingProtocol"]
