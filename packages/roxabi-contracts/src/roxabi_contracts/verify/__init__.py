"""ACL-verification contract surface.

Public API: SUBJECTS namespace + deny-probe subject helper. ``lyra.verify.deny``
is a reserved sentinel (ungranted negative-control), not a wire schema — see
``subjects`` for the rationale.
"""

from __future__ import annotations

from roxabi_contracts.verify.subjects import SUBJECTS, verify_deny

__all__ = [
    "SUBJECTS",
    "verify_deny",
]
