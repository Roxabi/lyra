"""Shared streaming session for channel adapters — re-export shim.

This module re-exports the full public API from the two focused submodules:
  - _shared_streaming_state   → IntermediateTextState, StreamState,
                                classify_stream_error
  - _shared_streaming_emitter → PlatformCallbacks, StreamingSession

Importers may continue to use ``from lyra.adapters._shared_streaming import ...``
without change.  New code should prefer importing directly from the submodule
that owns the symbol.
"""

from lyra.adapters._shared_streaming_emitter import (
    PlatformCallbacks,
    StreamingSession,
)
from lyra.adapters._shared_streaming_state import (
    STREAMING_EDIT_INTERVAL,
    IntermediateTextState,
    StreamState,
    classify_stream_error,
)

__all__ = [
    "STREAMING_EDIT_INTERVAL",
    "IntermediateTextState",
    "PlatformCallbacks",
    "StreamState",
    "StreamingSession",
    "classify_stream_error",
]
