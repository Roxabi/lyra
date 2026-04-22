"""Shared error-handling utilities for Lyra handlers and plugins.

Provides safe_error_response() — a single place to log exceptions and return
a generic user-facing message, preventing internal error details from leaking
to users.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lyra.core.messaging.message import GENERIC_ERROR_REPLY, Response

if TYPE_CHECKING:
    import logging


def safe_error_response(
    exc: Exception,
    logger: "logging.Logger",
    context: str = "",
) -> Response:
    """Log *exc* at ERROR level and return a generic safe Response.

    Prevents internal stack traces and error strings from being surfaced to
    users (C1 — error message information disclosure).

    Args:
        exc:     The caught exception.
        logger:  Module-level logger to use for the exception log entry.
        context: Human-readable label prepended to the log message
                 (e.g. ``"svc plugin"``).

    Returns:
        A ``Response`` with ``GENERIC_ERROR_REPLY`` as content.
    """
    logger.exception("%s: %s", context, exc)
    return Response(content=GENERIC_ERROR_REPLY)
