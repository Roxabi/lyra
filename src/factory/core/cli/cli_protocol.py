"""Backward-compatible re-export facade for cli_protocol.

The real implementation lives in protocol/cli_protocol and
protocol/cli_protocol_types.  Import from this module for the
full public surface.
"""

from __future__ import annotations

from .protocol.cli_protocol import (
    SESSION_ID_RE,
    CliProtocolOptions,
    CliResult,
    CliStreamingParser,
    StreamingIterator,
    build_cmd,
    read_until_result,
    send_and_read,
    send_and_read_stream,
)
from .protocol.cli_protocol_types import _read_stderr_snippet

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
    "_read_stderr_snippet",
]
