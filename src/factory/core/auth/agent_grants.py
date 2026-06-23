"""Agent-scoped authorization domain — pure models + the AgentAuthorizer port.

ADR-090: each named agent is a logical tenant owning an authorization matrix
(agent × principal × capability). Bots inherit access from their bound agent;
enforcement is hub-side, after binding resolution. This module holds the pure
domain types and the read port only — the SQLite implementation lives in
``factory.infrastructure.stores.identity.agent_grant_store`` (ADR-059).

No I/O here: the models are frozen dataclasses and the port is a Protocol.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol, runtime_checkable

__all__ = [
    "AgentAuthorizer",
    "AgentGrant",
    "AuthDecision",
    "Capability",
    "Principal",
    "PrincipalKind",
]


class PrincipalKind(str, Enum):
    """Whether a grant subject is an individual user or a platform role."""

    USER = "user"
    ROLE = "role"


class Capability(str, Enum):
    """What a principal may do within an agent tenancy.

    The MVP grants ``USE`` only; ``ADMIN`` is reserved by ADR-090 §1 for a
    deferred follow-up (privileged configuration) and is not enforced yet —
    ``AgentAuthorizer.authorize`` checks ``USE`` and never implies ``ADMIN``.
    """

    USE = "use"
    ADMIN = "admin"


@dataclass(frozen=True)
class Principal:
    """A grant subject: a platform-scoped user or role identity.

    ``id`` is the fully-qualified, platform-prefixed key the hub resolves from an
    inbound message — e.g. ``tg:user:7377831990``, ``dc:user:…``, ``dc:role:…``.
    Cross-platform alias resolution is deferred (ADR-090 §4): grants are
    platform-scoped at the MVP, so a ban on one platform does not propagate to a
    linked identity on another until aliasing lands.
    """

    kind: PrincipalKind
    id: str

    def __post_init__(self) -> None:
        # A principal with no id is nonsensical and, left unguarded, would let an
        # empty resolved user_id match an empty-id grant — so reject it at the
        # value-object boundary (fail-safe, ADR-090 §1).
        if not self.id:
            raise ValueError("Principal.id must be non-empty")


@dataclass(frozen=True)
class AgentGrant:
    """One row of the authorization matrix: (agent, principal, capability)."""

    agent_name: str
    principal: Principal
    capability: Capability
    granted_by: str
    source: str
    created_at: datetime


@dataclass(frozen=True)
class AuthDecision:
    """Outcome of an authorization check.

    Fail-safe by construction — callers build a denial with :meth:`deny` and an
    explicit reason, which the hub records on the ``agent_unauthorized`` audit
    event (ADR-090 §5). Truthiness mirrors :attr:`allowed` for ergonomic guards.
    """

    allowed: bool
    reason: str

    @classmethod
    def allow(cls, reason: str = "grant matched") -> AuthDecision:
        """Build an ALLOW decision."""
        return cls(allowed=True, reason=reason)

    @classmethod
    def deny(cls, reason: str = "no matching grant") -> AuthDecision:
        """Build a DENY decision (the fail-safe default)."""
        return cls(allowed=False, reason=reason)

    def __bool__(self) -> bool:
        return self.allowed


@runtime_checkable
class AgentAuthorizer(Protocol):
    """Read port: may a principal (user or any of their roles) reach an agent?

    The hub's ``AuthorizeAgentMiddleware`` (ADR-090 §5) depends on this narrow
    interface once the bound agent name is known. ``authorize`` is synchronous —
    it reads a warm in-memory cache and never blocks the event loop. Fail-safe:
    no matching ``use`` grant → deny.
    """

    def authorize(
        self,
        *,
        agent_name: str,
        user_id: str,
        roles: Sequence[str] = (),
    ) -> AuthDecision: ...
