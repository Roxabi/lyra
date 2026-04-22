"""Image-domain NATS subject strings and per-worker helpers.

Canonical values from ADR-044 §Subjects. Literal strings (no f-strings,
no derivation) so grep can locate every reference across the monorepo.
"""

import re
from dataclasses import dataclass
from typing import Literal

# NATS subject tokens are `.`-separated. ``*`` matches any single token and
# ``>`` matches a subtree. A worker id that contains any of those characters
# would inject wildcards into the published subject and let a subscriber
# claim more traffic than intended. Restrict to alphanumeric + ``-`` + ``_``.
_SAFE_WORKER_ID_RE = re.compile(r"[A-Za-z0-9_-]+")


@dataclass(frozen=True, slots=True)
class _Subjects:
    """Frozen namespace of image-domain subject strings.

    Attribute access is pyright-checked: typos fail at type-check time
    rather than silently returning None (cf. ADR-049 §API ergonomics).

    Each field is typed as a ``Literal[...]`` — a typo in the default
    value (e.g. ``"lyra.image.generate.reuqest"``) fails type-checking
    independently of the runtime string-equality assertions in
    ``tests/test_image_subjects.py``.
    """

    image_request: Literal["lyra.image.generate.request"] = (
        "lyra.image.generate.request"
    )
    image_heartbeat: Literal["lyra.image.heartbeat"] = "lyra.image.heartbeat"
    image_workers: Literal["IMAGE_WORKERS"] = "IMAGE_WORKERS"


SUBJECTS = _Subjects()


def validate_worker_id(worker_id: str) -> None:
    """Validate a worker_id against the NATS-subject-safe character class.

    Raises ``ValueError`` if ``worker_id`` contains anything outside
    ``[A-Za-z0-9_-]`` (notably ``. * >``, which are NATS wildcard or
    subtree delimiters). Used by ``per_worker_image`` on the PUBLISH
    path and by consumers on the heartbeat-receive path to keep the
    registry free of wildcard-injectable ids.
    """
    if not _SAFE_WORKER_ID_RE.fullmatch(worker_id):
        raise ValueError(
            f"worker_id must match [A-Za-z0-9_-]+ (got {worker_id!r}); "
            "NATS wildcard / subtree characters (. * >) are rejected to "
            "prevent subject injection"
        )


def per_worker_image(worker_id: str) -> str:
    """Per-worker image request subject: ``lyra.image.generate.request.{worker_id}``.

    Raises ``ValueError`` if ``worker_id`` contains characters outside
    ``[A-Za-z0-9_-]`` — see ``validate_worker_id``.
    """
    validate_worker_id(worker_id)
    return f"{SUBJECTS.image_request}.{worker_id}"
