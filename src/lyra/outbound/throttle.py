"""ThrottleCapability — typing-indicator + edit-debounce stage.

Composes into OutboundEmitter alongside Formatter and ErrorHandler. Per-platform
implementations live in adapters/{telegram,discord}/*_outbound.py and wrap the
existing typing-worker infrastructure (no behavior change).
"""

from __future__ import annotations

from typing import Protocol

# Seconds between intermediate streaming edits (debounce). Shared by all
# outbound emitters; aligned with each platform's rate-limit.
STREAMING_EDIT_INTERVAL = 1.0


class ThrottleCapability(Protocol):
    """Throttle stage for OutboundEmitter — typing indicator lifecycle + debounce."""

    edit_interval_s: float

    async def start_typing(self, scope_id: int) -> None: ...
    async def cancel_typing(self, scope_id: int) -> None: ...
