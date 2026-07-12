"""Control-plane identity models (dashboard auth) — pure, no I/O.

Orthogonal to chat-plane ``Principal`` / ``agent_grants`` (ADR-090).
ADR-103: session/API-key principal for BFF + hub dashboard RPC.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Literal

__all__ = [
    "ApiKeyRecord",
    "AuthVia",
    "ControlPlanePrincipal",
    "ControlPlaneUser",
    "GlobalRole",
    "InviteRecord",
    "InviteStatus",
    "SessionRecord",
    "UserStatus",
]

AuthVia = Literal["session", "api_key", "platform_link", "sys", "e2e"]


class GlobalRole(str, Enum):
    ADMIN = "admin"
    MEMBER = "member"


class UserStatus(str, Enum):
    ACTIVE = "active"
    DISABLED = "disabled"


class InviteStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REVOKED = "revoked"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class ControlPlanePrincipal:
    """Authenticated dashboard actor stamped onto control-plane acts."""

    user_id: str
    roles: frozenset[str]
    org_ids: frozenset[str]
    active_org_id: str | None
    via: AuthVia

    @property
    def is_admin(self) -> bool:
        return GlobalRole.ADMIN.value in self.roles

    def __post_init__(self) -> None:
        if not self.user_id:
            raise ValueError("ControlPlanePrincipal.user_id must be non-empty")


@dataclass(frozen=True, slots=True)
class ControlPlaneUser:
    id: str
    email: str | None
    display_name: str | None
    global_role: GlobalRole
    status: UserStatus
    created_at: datetime
    has_password: bool


@dataclass(frozen=True, slots=True)
class InviteRecord:
    id: str
    email: str
    invited_by: str
    status: InviteStatus
    expires_at: datetime
    created_at: datetime


@dataclass(frozen=True, slots=True)
class SessionRecord:
    id: str
    user_id: str
    expires_at: datetime
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ApiKeyRecord:
    id: str
    user_id: str
    name: str
    prefix: str
    created_at: datetime
    revoked_at: datetime | None
