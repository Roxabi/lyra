"""CLI error classification for blocking and streaming paths (ADR-089).

``CliStreamingParser`` and blocking ``CliPool.send()`` converge on
``_resolve_cli_worker_error()`` for structured ``WorkerError`` envelopes.
"""

from __future__ import annotations

import re

from roxabi_contracts.errors import KNOWN_CODES, WorkerError

__all__ = ["worker_error_from_cli_error", "_scrub_cli_error_text"]

# Subtypes that indicate an auth failure from the upstream CLI.
_AUTH_SUBTYPES = frozenset({"auth_error", "auth", "login_required"})
# Subtypes that suggest a lost / unresumable session.
_SESSION_LOST_SUBTYPES = frozenset({"session_expired", "session_lost", "resume_failed"})
# Provider quota / rate-limit failures (ADR-089 S6).
_RATE_LIMIT_SUBTYPES = frozenset(
    {"rate_limit_error", "rate_limit", "quota_exceeded", "usage_limit"}
)

# Max length of bus-bound CLI error messages. Upstream wire content is
# unbounded; trim before publishing to keep the NATS payload predictable.
_BUS_BOUND_MESSAGE_MAX_LEN = 200  # const-ok: bus-bound error message length cap

# Flat blocking CliResult.error strings carry no subtype; infer from message text.
_AUTH_FLAT_HINTS = (
    "not logged in",
    "login required",
    "auth error",
    "authentication required",
    "oauth",
)
_RATE_LIMIT_FLAT_HINTS = (
    "weekly limit",
    "rate limit",
    "quota exceeded",
    "usage limit",
    "hit your limit",
)
_SESSION_LOST_FLAT_HINTS = (
    "session expired",
    "session lost",
    "resume failed",
)


def _scrub_cli_error_text(text: str) -> str:
    """Bound length + strip control chars from bus-bound CLI error text.

    #1252 (sibling of #1212/#1215/#1219): path (a) — when the CLI reports
    ``is_error=True`` — sources error text verbatim from upstream wire
    fields (``result.errors[0]`` / ``result.result``). Without scrubbing,
    arbitrary bytes propagate to ``WorkerError.message`` and
    ``ResultLlmEvent.error_text`` (both bus-bound). Full diagnostic stays
    in ``log.warning`` at the call site.
    """
    if not text:
        return text
    scrubbed = "".join(c if c.isprintable() else " " for c in text)
    if len(scrubbed) > _BUS_BOUND_MESSAGE_MAX_LEN:
        scrubbed = scrubbed[: _BUS_BOUND_MESSAGE_MAX_LEN - 1] + "…"
    return scrubbed


def _hint_matches(lower: str, hint: str) -> bool:
    """Match a flat-error hint; single-token hints use word boundaries."""
    if " " in hint:
        return hint in lower
    return bool(re.search(rf"\b{re.escape(hint)}\b", lower))


def _infer_subtype_from_flat_error(error: str) -> str:
    """Infer a CLI result subtype from a flat blocking error string (ADR-089 S5)."""
    lower = error.lower()
    if any(_hint_matches(lower, hint) for hint in _AUTH_FLAT_HINTS):
        return "auth_error"
    if any(hint in lower for hint in _RATE_LIMIT_FLAT_HINTS):
        return "rate_limit_error"
    if any(hint in lower for hint in _SESSION_LOST_FLAT_HINTS):
        return "session_expired"
    return ""


def _subtype_is_specific(subtype: str) -> bool:
    return (
        subtype in _AUTH_SUBTYPES
        or subtype in _SESSION_LOST_SUBTYPES
        or subtype in _RATE_LIMIT_SUBTYPES
        or bool(subtype and "rate_limit" in subtype)
    )


def _classify_cli_error(subtype: str, error_text: str) -> WorkerError:
    """Map a CLI result subtype + message to a structured WorkerError."""
    if subtype in _AUTH_SUBTYPES:
        code = "cli.auth"
    elif subtype in _SESSION_LOST_SUBTYPES:
        code = "cli.session_lost"
    elif subtype in _RATE_LIMIT_SUBTYPES or "rate_limit" in subtype:
        code = "llm.rate_limit"
    else:
        code = "cli.parse"

    meta = KNOWN_CODES[code]
    return WorkerError(
        code=code,
        message=_scrub_cli_error_text(error_text) or meta.description,
        retryable=meta.default_retryable,
    )


def _resolve_cli_worker_error(subtype: str, error_text: str) -> WorkerError:
    """Classify CLI errors for blocking and streaming paths (ADR-089)."""
    lower = error_text.lower()
    if "timeout" in lower or "timed out" in lower:
        scrubbed = _scrub_cli_error_text(error_text)
        fallback = KNOWN_CODES["transport.timeout"].description
        return WorkerError(
            code="transport.timeout",
            message=scrubbed or fallback,
            retryable=True,
        )
    effective = subtype
    if not _subtype_is_specific(subtype):
        inferred = _infer_subtype_from_flat_error(error_text)
        if inferred:
            effective = inferred
    return _classify_cli_error(effective, error_text)


def worker_error_from_cli_error(error: str) -> WorkerError:
    """Map a blocking CliResult.error string to a structured WorkerError."""
    return _resolve_cli_worker_error("", error)
