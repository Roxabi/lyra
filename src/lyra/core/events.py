from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass(frozen=True)
class MonitoringEvent:
    timestamp: float = field(default_factory=time.monotonic)


@dataclass(frozen=True)
class AgentEvent(MonitoringEvent):
    agent_id: str = ""


@dataclass(frozen=True)
class AgentStarted(AgentEvent):
    scope_id: str | None = None


@dataclass(frozen=True)
class AgentCompleted(AgentEvent):
    duration_ms: float = 0.0


@dataclass(frozen=True)
class AgentFailed(AgentEvent):
    error: str = ""


@dataclass(frozen=True)
class AgentIdle(AgentEvent):
    finished_at: float = field(default_factory=time.monotonic)


@dataclass(frozen=True)
class CircuitStateChanged(MonitoringEvent):
    platform: str = ""
    old_state: str = ""
    new_state: str = ""


@dataclass(frozen=True)
class QueueDepthExceeded(MonitoringEvent):
    queue_name: str = ""
    depth: int = 0
    threshold: int = 0


@dataclass(frozen=True)
class QueueDepthNormal(MonitoringEvent):
    queue_name: str = ""
    depth: int = 0
