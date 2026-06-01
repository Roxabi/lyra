"""CLI protocol subpackage — re-export facade for backward compatibility."""

from __future__ import annotations

from .cli_protocol import (
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
from .cli_protocol_types import _read_stderr_snippet

__all__ = [
    "SESSION_ID_RE",
    "CliProtocolOptions",
    "CliResult",
    "CliStreamingParser",
    "StreamingIterator",
    "build_cmd",
    "read_until_result",
    "send_and_read",
    "send_and_read_stream",
    "_read_stderr_snippet",
]
