"""Shared types and constants for the message pipeline."""

from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..message import Response
    from ..pool import Pool

# Type alias for the optional trace hook.
# Called with (stage, event, **payload) at key pipeline decision points.
# The hook must be a plain synchronous callable — it is never awaited.
TraceHook = Callable[..., None]


class Action(enum.Enum):
    """Terminal action for the message pipeline."""

    DROP = "drop"
    COMMAND_HANDLED = "command_handled"
    SUBMIT_TO_POOL = "submit_to_pool"


class ResumeStatus(enum.Enum):
    """Outcome of _resolve_context() — how session continuity was handled.

    RESUMED  — a session was successfully resumed via any path.
    FRESH    — Path 2 (thread-session-resume) was attempted but rejected;
               Claude will start fresh. The user should be notified.
    SKIPPED  — no resume was attempted (pool busy, group chat, first use, etc.).
               Silent: this is expected behaviour.
    """

    RESUMED = "resumed"
    FRESH = "fresh"
    SKIPPED = "skipped"


SESSION_FALLTHROUGH_MSG = (
    "⚠️ Couldn't resume your previous session \u2014 starting fresh."
)

# Legacy alias — kept for any external code that may import the private name.
_SESSION_FALLTHROUGH_MSG = SESSION_FALLTHROUGH_MSG


@dataclass(frozen=True)
class PipelineResult:
    """Immutable result from the message pipeline."""

    action: Action
    response: Response | None = None
    pool: Pool | None = None


DROP = PipelineResult(action=Action.DROP)

# Legacy alias — kept for any external code that may import the private name.
_DROP = DROP

__all__ = [
    "Action",
    "DROP",
    "PipelineResult",
    "ResumeStatus",
    "SESSION_FALLTHROUGH_MSG",
    "TraceHook",
]
