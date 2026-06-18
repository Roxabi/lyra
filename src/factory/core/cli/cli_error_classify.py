"""CLI flat-error classification for blocking/in-process paths (ADR-089 S5).

Streaming errors are classified in ``CliStreamingParser._classify_cli_error``.
Blocking ``CliPool.send()`` returns only a flat ``CliResult.error`` string;
this helper synthesises the equivalent ``WorkerError`` envelope.
"""

from __future__ import annotations

from roxabi_contracts.errors import WorkerError

from .cli_streaming_parser import _resolve_cli_worker_error

__all__ = ["worker_error_from_cli_error"]


def worker_error_from_cli_error(error: str) -> WorkerError:
    """Map a blocking CliResult.error string to a structured WorkerError."""
    return _resolve_cli_worker_error("", error)