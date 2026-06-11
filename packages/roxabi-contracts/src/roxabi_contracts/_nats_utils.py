"""Shared NATS-protocol utilities for domain subject modules.

Domain-agnostic helpers that enforce the NATS-subject-safe character class.
Each per-domain ``subjects.py`` re-exports the relevant validators so
callers keep importing from their domain module; the single implementation
here prevents charset definitions from drifting across modules.
"""

import re

# SSoT for the NATS-subject-safe character class.
# ``_SAFE_SEGMENT_CHARS`` is the chars-only form (no brackets/quantifier)
# so it can be composed into negated character classes (e.g. re.sub).
# ``_SAFE_SEGMENT_RE`` is the compiled full-match regex.
# NATS subject tokens are `.`-separated. ``*`` matches any single token and
# ``>`` matches a subtree. Restrict to alphanumeric + ``-`` + ``_``.
_SAFE_SEGMENT_CHARS = r"A-Za-z0-9_-"
_SAFE_SEGMENT_RE = re.compile(f"[{_SAFE_SEGMENT_CHARS}]+")


def validate_worker_id(worker_id: str) -> None:
    """Validate a worker_id against the NATS-subject-safe character class.

    Raises ``ValueError`` if ``worker_id`` contains anything outside
    ``[A-Za-z0-9_-]`` (notably ``. * >``, which are NATS wildcard or
    subtree delimiters). Used by each domain's ``per_worker_*`` helper
    on the PUBLISH path and by consumers on the heartbeat-receive path
    to keep the registry free of wildcard-injectable ids.
    """
    if not _SAFE_SEGMENT_RE.fullmatch(worker_id):
        raise ValueError(
            f"worker_id must match [A-Za-z0-9_-]+ (got {worker_id!r}); "
            "NATS wildcard / subtree characters (. * >) are rejected to "
            "prevent subject injection"
        )


_SAFE_JOB_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*")


def validate_job_token(token: str) -> None:
    """Validate a job_name or job_id for NATS subject safety.
    Dots allowed for namespacing (vault.add-from-url) but leading/trailing/
    consecutive dots are rejected; * and > rejected."""
    if not _SAFE_JOB_TOKEN_RE.fullmatch(token):
        raise ValueError(
            f"job token must match [A-Za-z0-9_-]+([.][A-Za-z0-9_-]+)* (got {token!r}); "
            "NATS wildcard characters (* >) and dot-boundary violations are rejected"
        )


def validate_subject_segment(segment: str) -> None:
    """Validate a single NATS subject segment (no dots allowed).

    Raises ``ValueError`` if ``segment`` contains anything outside
    ``[A-Za-z0-9_-]``. Dots, wildcards (``* >``) and empty segments are
    all rejected. Promoted to the public API so external consumers and
    sanitizers can reuse the canonical charset check directly.
    """
    if not _SAFE_SEGMENT_RE.fullmatch(segment):
        raise ValueError(
            f"NATS subject segment must match [A-Za-z0-9_-]+ (got {segment!r}); "
            "dots, wildcards (* >) and empty segments are rejected"
        )


def validate_nats_subject(subject: str) -> None:
    """Validate a full multi-segment NATS subject (e.g. _INBOX.abc123).
    Splits on '.' and validates each segment via validate_subject_segment
    (no dots allowed per segment); rejects empty segments and wildcards."""
    if not subject:
        raise ValueError("NATS subject must not be empty")
    for segment in subject.split("."):
        validate_subject_segment(segment)
