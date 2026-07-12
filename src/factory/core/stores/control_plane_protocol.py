"""Control-plane identity directory protocol — pure port (ADR-059 / ADR-103).

``factory.dashboard`` depends on this Protocol only (not infrastructure stores).
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from factory.core.auth.control_plane import (
    ApiKeyRecord,
    ControlPlanePrincipal,
    ControlPlaneUser,
    GlobalRole,
    InviteRecord,
    SessionRecord,
)
from factory.core.auth.control_plane_org import OrgMember, OrgRecord, OrgRole

__all__ = ["ControlPlaneDirectory"]


@runtime_checkable
class ControlPlaneDirectory(Protocol):
    """User / invite / session / API-key directory for dashboard authn."""

    async def get_user(self, user_id: str) -> ControlPlaneUser | None: ...

    async def get_user_by_email(self, email: str) -> ControlPlaneUser | None: ...

    async def list_users(self) -> list[ControlPlaneUser]: ...

    async def create_user(
        self,
        *,
        email: str,
        password: str,
        global_role: GlobalRole = GlobalRole.MEMBER,
        display_name: str | None = None,
        user_id: str | None = None,
    ) -> ControlPlaneUser: ...

    async def set_password(self, user_id: str, password: str) -> None: ...

    async def verify_password(
        self, email: str, password: str
    ) -> ControlPlaneUser | None:
        """Return active user when credentials match; else None."""
        ...

    async def set_user_status(self, user_id: str, status: str) -> None: ...

    async def bootstrap_admin_if_empty(
        self,
        *,
        email: str,
        password: str,
        display_name: str = "Admin",
    ) -> ControlPlaneUser | None:
        """Create the first admin when no active admin exists. Idempotent."""
        ...

    # --- invites ---

    async def create_invite(
        self,
        *,
        email: str,
        invited_by: str,
        expires_at: datetime,
        raw_token: str | None = None,
    ) -> tuple[InviteRecord, str]:
        """Create pending invite. Returns (record, plaintext_token once)."""
        ...

    async def get_invite(self, invite_id: str) -> InviteRecord | None: ...

    async def list_invites(
        self, *, status: str | None = None
    ) -> list[InviteRecord]: ...

    async def revoke_invite(self, invite_id: str) -> bool: ...

    async def accept_invite(
        self,
        *,
        raw_token: str,
        password: str,
        display_name: str | None = None,
    ) -> ControlPlaneUser:
        """Consume invite token → active member user. Raises ValueError on failure."""
        ...

    # --- sessions ---

    async def create_session(
        self,
        user_id: str,
        *,
        ttl_seconds: int = 1_209_600,  # const-ok: default session TTL 14d
    ) -> tuple[SessionRecord, str]:
        """Create session. Returns (record, plaintext_token once)."""
        ...

    async def resolve_session(self, raw_token: str) -> ControlPlanePrincipal | None: ...

    async def revoke_session(self, raw_token: str) -> bool: ...

    async def revoke_user_sessions(self, user_id: str) -> int: ...

    # --- API keys ---

    async def create_api_key(
        self,
        user_id: str,
        *,
        name: str,
    ) -> tuple[ApiKeyRecord, str]:
        """Create API key. Returns (record, plaintext_secret once)."""
        ...

    async def list_api_keys(self, user_id: str) -> list[ApiKeyRecord]: ...

    async def revoke_api_key(
        self, key_id: str, *, user_id: str | None = None
    ) -> bool: ...

    async def resolve_api_key(self, raw_key: str) -> ControlPlanePrincipal | None: ...

    async def principal_for_user(
        self,
        user: ControlPlaneUser,
        *,
        via: str,
        active_org_id: str | None = None,
    ) -> ControlPlanePrincipal: ...

    # --- organizations ---

    async def create_org(self, *, name: str, created_by: str) -> OrgRecord: ...

    async def get_org(self, org_id: str) -> OrgRecord | None: ...

    async def list_orgs_for_user(self, user_id: str) -> list[OrgRecord]: ...

    async def org_ids_for_user(self, user_id: str) -> frozenset[str]: ...

    async def is_org_member(self, org_id: str, user_id: str) -> bool: ...

    async def add_org_member(
        self,
        org_id: str,
        user_id: str,
        *,
        org_role: OrgRole = OrgRole.MEMBER,
        actor_user_id: str,
        actor_is_admin: bool = False,
    ) -> OrgMember: ...

    async def remove_org_member(
        self,
        org_id: str,
        user_id: str,
        *,
        actor_user_id: str,
        actor_is_admin: bool = False,
    ) -> bool: ...

    async def list_org_members(self, org_id: str) -> list[OrgMember]: ...

    # --- platform links ---

    async def create_link_code(
        self,
        user_id: str,
        *,
        platform: str | None = None,
        ttl_seconds: int = 600,  # const-ok: 10m link TTL
    ) -> tuple[str, str]: ...

    async def consume_link_code(
        self, raw_token: str, *, platform_key: str
    ) -> str: ...

    async def list_platform_links(self, user_id: str) -> list[dict[str, str]]: ...

    async def chat_ready(self, user_id: str) -> bool: ...

    async def unlink_platform(self, user_id: str, platform: str) -> bool: ...

    async def is_platform_chat_ready(self, platform_key: str) -> bool: ...

    async def dash_user_for_platform(self, platform_key: str) -> str | None: ...
