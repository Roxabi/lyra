"""CliPool-domain NATS subject strings and queue group.

Canonical values from ADR-054 (absorbed into ADR-055).
Literal strings (no f-strings, no derivation) so grep can locate every reference
across the monorepo.
"""

from dataclasses import dataclass
from typing import Literal

__all__ = ["SUBJECTS"]


@dataclass(frozen=True, slots=True)
class _Subjects:
    """Frozen namespace of CliPool-domain subject strings.

    Attribute access is pyright-checked: typos fail at type-check time
    rather than silently returning None.
    """

    cmd: Literal["factory.jobs.claude"] = "factory.jobs.claude"
    control: Literal["factory.clipool.control"] = "factory.clipool.control"
    heartbeat: Literal["factory.clipool.heartbeat"] = "factory.clipool.heartbeat"
    clipool_workers: Literal["clipool-workers"] = "clipool-workers"


SUBJECTS = _Subjects()
