"""Typed pipeline telemetry events (#432).

Frozen dataclasses emitted at middleware seam boundaries.
The ``PipelineEventBus`` fans these out to subscribers (audit logger,
future metrics, dashboard, trace propagation).

Event types:
  MessageReceived   — inbound message enters the pipeline
  StageCompleted    — a middleware stage finished (subtree-inclusive timing)
  MessageDropped    — a middleware stage short-circuited with DROP
  CommandDispatched — CommandMiddleware dispatched a command
  PoolSubmitted     — SubmitToPoolMiddleware submitted to a pool
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass(frozen=True)
class PipelineEvent:
    """Base for all pipeline telemetry events."""

    msg_id: str
    timestamp: float = field(default_factory=time.time)
    stage: str = ""


@dataclass(frozen=True)
class MessageReceived(PipelineEvent):
    """Emitted when a message enters the pipeline."""

    platform: str = ""
    user_id: str = ""
    scope_id: str = ""


@dataclass(frozen=True)
class StageCompleted(PipelineEvent):
    """Emitted after each middleware stage completes.

    ``duration_ms`` is subtree-inclusive — it measures the stage's total
    latency contribution including downstream stages called via ``next()``.
    """

    duration_ms: float = 0.0


@dataclass(frozen=True)
class MessageDropped(PipelineEvent):
    """Emitted when a middleware stage drops a message."""

    reason: str = ""


@dataclass(frozen=True)
class CommandDispatched(PipelineEvent):
    """Emitted when CommandMiddleware dispatches a command."""

    command: str = ""


@dataclass(frozen=True)
class PoolSubmitted(PipelineEvent):
    """Emitted when SubmitToPoolMiddleware submits to a pool."""

    pool_id: str = ""
    agent_name: str = ""
    resume_status: str = ""
