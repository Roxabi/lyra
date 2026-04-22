"""Guard protocol, BlockedGuard, and GuardChain for composable policy enforcement.

Guards enforce access policy on resolved Identity objects. A guard returns
Rejection to reject, or None to pass. GuardChain runs guards sequentially
and short-circuits on the first Rejection.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from lyra.core.auth.identity import Identity
from lyra.core.auth.trust import TrustLevel

__all__ = ["BlockedGuard", "Guard", "GuardChain", "Rejection"]


@dataclass(frozen=True)
class Rejection:
    """Returned by a guard to reject. None = pass, Rejection = rejected."""

    reason: str


@runtime_checkable
class Guard(Protocol):
    """Protocol for composable access guards."""

    def check(self, identity: Identity) -> Rejection | None:
        """Return Rejection to reject, or None to pass."""
        ...


class BlockedGuard:
    """Rejects identities with TrustLevel.BLOCKED."""

    def check(self, identity: Identity) -> Rejection | None:
        if identity.trust_level == TrustLevel.BLOCKED:
            return Rejection(reason="blocked")
        return None


class GuardChain:
    """Runs guards sequentially, returning the first Rejection or None."""

    def __init__(self, guards: Sequence[Guard]) -> None:
        self._guards = list(guards)

    def run(self, identity: Identity) -> Rejection | None:
        """Evaluate guards in order. Short-circuit on first rejection."""
        for guard in self._guards:
            rejection = guard.check(identity)
            if rejection is not None:
                return rejection
        return None
