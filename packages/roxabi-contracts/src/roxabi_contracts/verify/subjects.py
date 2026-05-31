"""ACL-verification NATS subject strings and helper.

``lyra.verify.deny`` is a *reserved sentinel* — NOT a wire contract. It exists
solely so ``lyra ops verify`` can publish a negative-control probe
(``lyra.verify.deny.<identity>``) that every identity's ACL MUST deny. It is
intentionally granted to no one in ``deploy/nats/acl-matrix.json``; declaring it
here gives the subject a single source of truth and makes the CodeInventory
oracle aware of it without implying a publish/subscribe grant.

Canonical value is a literal string (no f-strings, no derivation) so grep can
locate every reference across the monorepo.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from roxabi_contracts._nats_utils import _validate_subject_segment

__all__ = [
    "SUBJECTS",
    "verify_deny",
]


@dataclass(frozen=True, slots=True)
class _Subjects:
    """Frozen namespace for the ACL-verification probe subject.

    Holds the static prefix half of the deny-probe subject family. The dynamic
    suffix (identity name) is appended by the helper function below.
    """

    deny_prefix: Literal["lyra.verify.deny"] = "lyra.verify.deny"


SUBJECTS = _Subjects()


def verify_deny(identity: str) -> str:
    """Deny-probe subject: ``lyra.verify.deny.<identity>``.

    Raises ``ValueError`` if ``identity`` contains characters outside
    ``[A-Za-z0-9_-]`` (the acl-matrix identity charset).
    """
    _validate_subject_segment(identity)
    return f"{SUBJECTS.deny_prefix}.{identity}"
