"""Turns-domain NATS subject strings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

__all__ = ["SUBJECTS"]


@dataclass(frozen=True, slots=True)
class _Subjects:
    """Frozen namespace for turns-domain subject literals."""

    turn_write: Literal["lyra.turns.write"] = "lyra.turns.write"


SUBJECTS = _Subjects()
