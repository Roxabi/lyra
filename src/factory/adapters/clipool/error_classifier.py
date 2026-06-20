"""Clipool worker exception → WorkerError mapping (#1962)."""

from __future__ import annotations

import asyncio

from factory.core.cli.cli_error_classify import worker_error_from_cli_error
from roxabi_contracts.errors import WorkerError


def classify_exception(exc: BaseException) -> WorkerError:
    """Map an exception to a ``WorkerError`` with the appropriate code.

    Code selection (per T13 / ADR-066 (absorbed into ADR-049)):
    - ``asyncio.TimeoutError`` → ``cli.session_lost`` (retryable=True)
    - ``UnicodeDecodeError`` / ``ValueError`` (parse/decode) → ``cli.parse``
      (retryable=False)
    - Any other exception → ``worker.crash`` (retryable=True)
    """
    # Sanitize bus-bound messages (#1215, sibling of #1212). Exception __str__
    # can embed file paths, byte sequences, or arbitrary text from system
    # errors — the bus-bound message keeps only the type name. Full traceback
    # logging is owned by the callers (_handle_cmd_streaming/_handle_cmd_blocking
    # both call log.exception before invoking this classifier).
    if isinstance(exc, asyncio.TimeoutError):
        return WorkerError(
            code="cli.session_lost",
            message=f"CLI session timed out: {type(exc).__name__}",
            retryable=True,
        )
    if isinstance(exc, (UnicodeDecodeError, ValueError)):
        return WorkerError(
            code="cli.parse",
            message=f"CLI parse/decode error: {type(exc).__name__}",
            retryable=False,
        )
    return WorkerError(
        code="worker.crash",
        message=f"Unhandled worker exception: {type(exc).__name__}",
        retryable=True,
    )


def worker_error_from_cli_result(error: str) -> WorkerError:
    """Synthesise a WorkerError for blocking CliPool.send() failures."""
    return worker_error_from_cli_error(error)
