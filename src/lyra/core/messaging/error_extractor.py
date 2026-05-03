"""Hub-side WorkerError extractor (ADR-066 § "Hub side").

Single entry-point: ``_extract_worker_error`` — safe across all reply
envelope types, including legacy ones that predate the field (e.g.
``CliControlAck``).
"""

from __future__ import annotations

import logging

from roxabi_contracts.errors import WorkerError

log = logging.getLogger(__name__)

__all__ = ["_extract_worker_error"]


def _extract_worker_error(reply: object) -> WorkerError | None:
    """Extract WorkerError from any reply envelope. None if absent.

    Safe across legacy envelopes that don't carry the field (e.g. CliControlAck).
    Logs a warning if is_error=False contradicts a populated worker_error,
    but still returns the WorkerError (it overrides is_error).
    """
    we = getattr(reply, "worker_error", None)
    if we is None:
        return None
    is_err = getattr(reply, "is_error", None)
    ok_flag = getattr(reply, "ok", None)
    # Contradiction: success flag set but worker_error populated → WE wins, log warning.
    # Use `is` identity (not truthiness): legacy envelopes lack the field and surface
    # as `None` here, which must NOT be treated as "success". `is False` matches only
    # an explicit False; `is True` matches only an explicit True. Do NOT collapse to
    # `not is_err` / `ok_flag` — that would fire false positives on every legacy reply.
    contradicts = (is_err is False) or (ok_flag is True)
    if contradicts:
        log.warning(
            "envelope contradiction: is_error/ok says success but worker_error "
            "populated (code=%s, type=%s)",
            we.code,
            type(reply).__name__,
        )
    return we
