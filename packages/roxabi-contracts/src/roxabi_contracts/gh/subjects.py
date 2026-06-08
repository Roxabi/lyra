"""GitHub-domain NATS subject strings and helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from roxabi_contracts._nats_utils import _validate_subject_segment

__all__ = [
    "SUBJECTS",
    "gh_mint_failure",
]


@dataclass(frozen=True, slots=True)
class _Subjects:
    """Frozen namespace for gh-domain subject prefixes.

    Holds the static prefix half of the mint-failure subject family.
    The dynamic suffix (machine) is appended by the helper function below.
    """

    mint_failure_prefix: Literal["factory.gh.mint_failure"] = "factory.gh.mint_failure"


SUBJECTS = _Subjects()


def gh_mint_failure(machine: str) -> str:
    """Mint-failure subject: factory.gh.mint_failure.<machine>.

    ``machine`` must be a single subject segment (no dots) so the result is
    always 4 segments — see ``_validate_subject_segment`` (#1708).
    """
    _validate_subject_segment(machine)
    return f"factory.gh.mint_failure.{machine}"
