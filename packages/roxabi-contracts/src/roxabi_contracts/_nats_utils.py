"""Shared NATS-protocol utilities for domain subject modules.

Domain-agnostic helpers (``validate_worker_id`` + the regex it uses) that
enforce the NATS-subject-safe character class. Each per-domain
``subjects.py`` re-exports ``validate_worker_id`` so callers keep
importing from their domain module; the single implementation here
prevents the two definitions from drifting.
"""

import re

# NATS subject tokens are `.`-separated. ``*`` matches any single token and
# ``>`` matches a subtree. A worker id that contains any of those characters
# would inject wildcards into the published subject and let a subscriber
# claim more traffic than intended. Restrict to alphanumeric + ``-`` + ``_``.
_SAFE_WORKER_ID_RE = re.compile(r"[A-Za-z0-9_-]+")


def validate_worker_id(worker_id: str) -> None:
    """Validate a worker_id against the NATS-subject-safe character class.

    Raises ``ValueError`` if ``worker_id`` contains anything outside
    ``[A-Za-z0-9_-]`` (notably ``. * >``, which are NATS wildcard or
    subtree delimiters). Used by each domain's ``per_worker_*`` helper
    on the PUBLISH path and by consumers on the heartbeat-receive path
    to keep the registry free of wildcard-injectable ids.
    """
    if not _SAFE_WORKER_ID_RE.fullmatch(worker_id):
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


# Subject *segments* (post-split) must not contain dots; use _SAFE_WORKER_ID_RE
# rather than _SAFE_JOB_TOKEN_RE to keep the two validation paths independent.
_SAFE_SUBJECT_SEGMENT_RE = re.compile(r"[A-Za-z0-9_-]+")


def _validate_subject_segment(segment: str) -> None:
    if not _SAFE_SUBJECT_SEGMENT_RE.fullmatch(segment):
        raise ValueError(
            f"NATS subject segment must match [A-Za-z0-9_-]+ (got {segment!r}); "
            "dots, wildcards (* >) and empty segments are rejected"
        )


def validate_nats_subject(subject: str) -> None:
    """Validate a full multi-segment NATS subject (e.g. _INBOX.abc123).
    Splits on '.' and validates each segment via _validate_subject_segment
    (no dots allowed per segment); rejects empty segments and wildcards."""
    if not subject:
        raise ValueError("NATS subject must not be empty")
    for segment in subject.split("."):
        _validate_subject_segment(segment)
