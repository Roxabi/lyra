"""Transitional shim — content relocated to lyra.outbound.emitter (issue #1279).

This module re-exports the names that used to live here so existing imports
keep resolving while consumers migrate. Deleted at S7 of #1279.
"""

from __future__ import annotations

from lyra.outbound.emitter import (
    OutboundEmitter as StreamingSession,
)
from lyra.outbound.emitter import (
    PlatformCallbacks,
    _default_no_op_edit_reasoning,
    _default_no_op_edit_tool_recap,
    _prepend,
)

__all__ = [
    "StreamingSession",
    "PlatformCallbacks",
    "_default_no_op_edit_reasoning",
    "_default_no_op_edit_tool_recap",
    "_prepend",
]
