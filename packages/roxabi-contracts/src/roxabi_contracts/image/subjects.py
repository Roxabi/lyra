"""Image-domain NATS subject strings and per-worker helpers.

Canonical values from ADR-044 §Subjects. Literal strings (no f-strings,
no derivation) so grep can locate every reference across the monorepo.
"""

from dataclasses import dataclass
from typing import Literal

from roxabi_contracts._nats_utils import validate_worker_id

__all__ = ["SUBJECTS", "per_worker_image", "validate_worker_id"]


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
    image_workers: Literal["image_workers"] = "image_workers"


SUBJECTS = _Subjects()


def per_worker_image(worker_id: str) -> str:
    """Per-worker image request subject: ``lyra.image.generate.request.{worker_id}``.

    Raises ``ValueError`` if ``worker_id`` contains characters outside
    ``[A-Za-z0-9_-]`` — see ``validate_worker_id``.
    """
    validate_worker_id(worker_id)
    return f"{SUBJECTS.image_request}.{worker_id}"
