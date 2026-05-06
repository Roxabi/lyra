"""GitHub-domain NATS contract surface.

Public API: MintFailureEvent envelope model + SUBJECTS namespace + subject helper.
fixtures submodule is test-only — import explicitly as
``from roxabi_contracts.gh.fixtures import ...``.
"""

from __future__ import annotations

from roxabi_contracts.gh.models import MintFailureEvent
from roxabi_contracts.gh.subjects import SUBJECTS, gh_mint_failure

__all__ = [
    "MintFailureEvent",
    "SUBJECTS",
    "gh_mint_failure",
]
