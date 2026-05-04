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


_SAFE_JOB_TOKEN_RE = re.compile(r"[A-Za-z0-9._-]+")


def validate_job_token(token: str) -> None:
    """Validate a job_name or job_id for NATS subject safety.
    Dots allowed (namespacing: vault.add-from-url); * and > rejected."""
    if not _SAFE_JOB_TOKEN_RE.fullmatch(token):
        raise ValueError(
            f"job token must match [A-Za-z0-9._-]+ (got {token!r}); "
            "NATS wildcard characters (* >) and spaces are rejected"
        )
