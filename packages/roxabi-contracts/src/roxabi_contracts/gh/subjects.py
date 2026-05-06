"""GitHub-domain NATS subject strings and helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from roxabi_contracts._nats_utils import validate_job_token

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

    mint_failure_prefix: Literal["lyra.gh.mint_failure"] = "lyra.gh.mint_failure"


SUBJECTS = _Subjects()


def gh_mint_failure(machine: str) -> str:
    """Mint-failure subject: lyra.gh.mint_failure.<machine>."""
    validate_job_token(machine)
    return f"lyra.gh.mint_failure.{machine}"
