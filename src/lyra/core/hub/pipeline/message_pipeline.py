"""Backward-compatibility shim — re-exports everything from pipeline_types.

All new code should import directly from `pipeline_types`.
This module is retained so that existing tests and external importers
that reference ``lyra.core.hub.message_pipeline`` continue to work
without modification.
"""

from .pipeline_types import (  # noqa: F401
    DROP,
    SESSION_FALLTHROUGH_MSG,
    Action,
    PipelineResult,
    ResumeStatus,
    TraceHook,
)

# Legacy private-name aliases.
_DROP = DROP
_SESSION_FALLTHROUGH_MSG = SESSION_FALLTHROUGH_MSG

__all__ = [
    "Action",
    "DROP",
    "_DROP",
    "PipelineResult",
    "ResumeStatus",
    "SESSION_FALLTHROUGH_MSG",
    "_SESSION_FALLTHROUGH_MSG",
    "TraceHook",
]
