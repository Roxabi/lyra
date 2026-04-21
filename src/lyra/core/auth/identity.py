"""Identity — resolved identity for an inbound message sender."""

from __future__ import annotations

from dataclasses import dataclass

from lyra.core.auth.trust import TrustLevel

__all__ = ["Identity"]


@dataclass(frozen=True)
class Identity:
    """Resolved identity produced by Authenticator.resolve().

    Carries the user's trust level and admin status so downstream
    guards and handlers can inspect a single object instead of
    re-resolving from multiple sources.
    """

    user_id: str
    trust_level: TrustLevel
    is_admin: bool
