"""Pipeline sub-package for types, events, and pool management."""

from .pipeline_events import (
    CommandDispatched,
    MessageDropped,
    MessageReceived,
    PipelineEvent,
    PoolSubmitted,
    StageCompleted,
)
from .pipeline_types import DROP, Action, PipelineResult, ResumeStatus
from .pool_manager import PoolManager

__all__ = [
    "Action",
    "DROP",
    "PipelineResult",
    "ResumeStatus",
    "PipelineEvent",
    "MessageReceived",
    "StageCompleted",
    "MessageDropped",
    "CommandDispatched",
    "PoolSubmitted",
    "PoolManager",
]
