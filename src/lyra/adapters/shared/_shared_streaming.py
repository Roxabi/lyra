"""Shared streaming session for channel adapters — re-export shim.

This module re-exports the full public API from the two focused submodules:
  - _shared_streaming_state   → IntermediateTextState, StreamState,
                                classify_stream_error
  - _shared_streaming_emitter → PlatformCallbacks, StreamingSession

Importers may continue to use ``from lyra.adapters.shared._shared_streaming import ...``
without change.
"""

from lyra.adapters.shared._shared_streaming_emitter import (
    PlatformCallbacks,
    StreamingSession,
)
from lyra.adapters.shared._shared_streaming_state import (
    IntermediateTextState,
    StreamState,
    classify_stream_error,
)

__all__ = [
    "IntermediateTextState",
    "PlatformCallbacks",
    "StreamState",
    "StreamingSession",
    "classify_stream_error",
]
