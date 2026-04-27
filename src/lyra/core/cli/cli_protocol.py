"""NDJSON protocol layer for the Claude CLI subprocess.

Public re-export facade.  Shared types live in cli_protocol_types; the
protocol implementations live in cli_non_streaming and cli_streaming.
Import from this module for the full public surface.
"""

from __future__ import annotations

from .cli_non_streaming import read_until_result, send_and_read
from .cli_protocol_types import (
    SESSION_ID_RE,
    CliProtocolOptions,
    CliResult,
    build_cmd,
)
from .cli_streaming import CliStreamingParser, StreamingIterator, send_and_read_stream

__all__ = [
    "CliProtocolOptions",
    "CliResult",
    "CliStreamingParser",
    "SESSION_ID_RE",
    "StreamingIterator",
    "build_cmd",
    "read_until_result",
    "send_and_read",
    "send_and_read_stream",
]
