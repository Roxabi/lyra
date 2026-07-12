"""Invite lifecycle for ControlPlaneStore."""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from factory.core.auth.control_plane import (
    ControlPlaneUser,
    GlobalRole,
    InviteRecord,
    InviteStatus,
)
from factory.infrastructure.stores.identity.control_plane_ddl import (
    _normalize_email,
    _parse_ts,
    _utc_now,
)
from factory.infrastructure.stores.identity.password_hash import hash_token

if TYPE_CHECKING:
    import aiosqlite


class ControlPlaneInviteOps:
    """Mixin: invite create / list / revoke / accept."""

    if TYPE_CHECKING:

        def _require_db(self) -> aiosqlite.Connection: ...

        async def get_user_by_email(self, email: str) -> ControlPlaneUser | None: ...

        async def create_user(
            self,
            *,
            email: str,
            password: str,
            global_role: GlobalRole = GlobalRole.MEMBER,
            display_name: str | None = None,
            user_id: str | None = None,
        ) -> ControlPlaneUser: ...

    async def create_invite(
        self,
        *,
        email: str,
        invited_by: str,
        expires_at: datetime,
        raw_token: str | None = None,
    ) -> tuple[InviteRecord, str]:
        normalized = _normalize_email(email)
        if not normalized:
            raise ValueError("email must be non-empty")
        if await self.get_user_by_email(normalized) is not None:
            raise ValueError(f"user already exists for {normalized}")
        token = raw_token or secrets.token_urlsafe(32)
        invite_id = f"inv:{uuid4().hex}"
        now = _utc_now()
        db = self._require_db()
        await db.execute(
            "INSERT INTO dash_invites "
            "(id, email, token_hash, invited_by, status, expires_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                invite_id,
                normalized,
                hash_token(token),
                invited_by,
                InviteStatus.PENDING.value,
                expires_at.isoformat(),
                now.isoformat(),
            ),
        )
        await db.commit()
        rec = InviteRecord(
            id=invite_id,
            email=normalized,
            invited_by=invited_by,
            status=InviteStatus.PENDING,
            expires_at=expires_at,
            created_at=now,
        )
        return rec, token

    async def get_invite(self, invite_id: str) -> InviteRecord | None:
        db = self._require_db()
        async with db.execute(
            "SELECT id, email, invited_by, status, expires_at, created_at "
            "FROM dash_invites WHERE id = ?",
            (invite_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return InviteRecord(
            id=row[0],
            email=row[1],
            invited_by=row[2],
            status=InviteStatus(row[3]),
            expires_at=_parse_ts(row[4]),
            created_at=_parse_ts(row[5]),
        )

    async def list_invites(self, *, status: str | None = None) -> list[InviteRecord]:
        db = self._require_db()
        if status:
            q = (
                "SELECT id, email, invited_by, status, expires_at, created_at "
                "FROM dash_invites WHERE status = ? ORDER BY created_at DESC"
            )
            params: tuple = (status,)
        else:
            q = (
                "SELECT id, email, invited_by, status, expires_at, created_at "
                "FROM dash_invites ORDER BY created_at DESC"
            )
            params = ()
        async with db.execute(q, params) as cur:
            rows = await cur.fetchall()
        return [
            InviteRecord(
                id=r[0],
                email=r[1],
                invited_by=r[2],
                status=InviteStatus(r[3]),
                expires_at=_parse_ts(r[4]),
                created_at=_parse_ts(r[5]),
            )
            for r in rows
        ]

    async def revoke_invite(self, invite_id: str) -> bool:
        db = self._require_db()
        async with db.execute(
            "UPDATE dash_invites SET status = ? "
            "WHERE id = ? AND status = ?",
            (InviteStatus.REVOKED.value, invite_id, InviteStatus.PENDING.value),
        ) as cur:
            changed = cur.rowcount
        await db.commit()
        return changed > 0

    async def accept_invite(
        self,
        *,
        raw_token: str,
        password: str,
        display_name: str | None = None,
    ) -> ControlPlaneUser:
        if not raw_token or not password:
            raise ValueError("token and password required")
        db = self._require_db()
        th = hash_token(raw_token)
        async with db.execute(
            "SELECT id, email, status, expires_at FROM dash_invites "
            "WHERE token_hash = ?",
            (th,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise ValueError("invalid invite token")
        invite_id, email, status, expires_at_s = row
        if status != InviteStatus.PENDING.value:
            raise ValueError(f"invite not pending ({status})")
        expires_at = _parse_ts(expires_at_s)
        if _utc_now() > expires_at:
            await db.execute(
                "UPDATE dash_invites SET status = ? WHERE id = ?",
                (InviteStatus.EXPIRED.value, invite_id),
            )
            await db.commit()
            raise ValueError("invite expired")
        if await self.get_user_by_email(email) is not None:
            raise ValueError("email already registered")
        user = await self.create_user(
            email=email,
            password=password,
            global_role=GlobalRole.MEMBER,
            display_name=display_name,
        )
        await db.execute(
            "UPDATE dash_invites SET status = ? WHERE id = ?",
            (InviteStatus.ACCEPTED.value, invite_id),
        )
        await db.commit()
        return user
