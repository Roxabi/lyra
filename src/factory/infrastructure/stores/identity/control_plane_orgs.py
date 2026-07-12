"""Organization + membership ops for ControlPlaneStore (ADR-103 Block 4)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from factory.core.auth.control_plane_org import OrgMember, OrgRecord, OrgRole
from factory.infrastructure.stores.identity.control_plane_ddl import (
    _parse_ts,
    _utc_now,
)

if TYPE_CHECKING:
    import aiosqlite


class ControlPlaneOrgOps:
    """Mixin: organizations and memberships."""

    if TYPE_CHECKING:

        def _require_db(self) -> aiosqlite.Connection: ...

    async def create_org(self, *, name: str, created_by: str) -> OrgRecord:
        label = name.strip()
        if not label:
            raise ValueError("org name must be non-empty")
        org_id = f"org:{uuid4().hex}"
        now = _utc_now()
        db = self._require_db()
        await db.execute(
            "INSERT INTO organizations (id, name, created_by, created_at) "
            "VALUES (?, ?, ?, ?)",
            (org_id, label, created_by, now.isoformat()),
        )
        await db.execute(
            "INSERT INTO org_members (org_id, user_id, org_role) VALUES (?, ?, ?)",
            (org_id, created_by, OrgRole.OWNER.value),
        )
        await db.commit()
        return OrgRecord(
            id=org_id, name=label, created_by=created_by, created_at=now
        )

    async def get_org(self, org_id: str) -> OrgRecord | None:
        db = self._require_db()
        async with db.execute(
            "SELECT id, name, created_by, created_at FROM organizations WHERE id = ?",
            (org_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return OrgRecord(
            id=row[0],
            name=row[1],
            created_by=row[2],
            created_at=_parse_ts(row[3]),
        )

    async def list_orgs_for_user(self, user_id: str) -> list[OrgRecord]:
        db = self._require_db()
        async with db.execute(
            "SELECT o.id, o.name, o.created_by, o.created_at "
            "FROM organizations o "
            "JOIN org_members m ON m.org_id = o.id "
            "WHERE m.user_id = ? ORDER BY o.created_at",
            (user_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [
            OrgRecord(
                id=r[0], name=r[1], created_by=r[2], created_at=_parse_ts(r[3])
            )
            for r in rows
        ]

    async def org_ids_for_user(self, user_id: str) -> frozenset[str]:
        db = self._require_db()
        async with db.execute(
            "SELECT org_id FROM org_members WHERE user_id = ?",
            (user_id,),
        ) as cur:
            rows = await cur.fetchall()
        return frozenset(r[0] for r in rows)

    async def is_org_member(self, org_id: str, user_id: str) -> bool:
        db = self._require_db()
        async with db.execute(
            "SELECT 1 FROM org_members WHERE org_id = ? AND user_id = ?",
            (org_id, user_id),
        ) as cur:
            return await cur.fetchone() is not None

    async def add_org_member(
        self,
        org_id: str,
        user_id: str,
        *,
        org_role: OrgRole = OrgRole.MEMBER,
        actor_user_id: str,
        actor_is_admin: bool = False,
    ) -> OrgMember:
        if not await self.get_org(org_id):
            raise ValueError(f"org not found: {org_id}")
        if not actor_is_admin:
            role = await self._member_role(org_id, actor_user_id)
            if role != OrgRole.OWNER:
                raise PermissionError("only org owner or admin may add members")
        db = self._require_db()
        await db.execute(
            "INSERT INTO org_members (org_id, user_id, org_role) VALUES (?, ?, ?) "
            "ON CONFLICT(org_id, user_id) DO UPDATE SET org_role = excluded.org_role",
            (org_id, user_id, org_role.value),
        )
        await db.commit()
        return OrgMember(org_id=org_id, user_id=user_id, org_role=org_role)

    async def remove_org_member(
        self,
        org_id: str,
        user_id: str,
        *,
        actor_user_id: str,
        actor_is_admin: bool = False,
    ) -> bool:
        if not actor_is_admin:
            role = await self._member_role(org_id, actor_user_id)
            if role != OrgRole.OWNER and actor_user_id != user_id:
                raise PermissionError("only owner/admin may remove members")
        db = self._require_db()
        async with db.execute(
            "DELETE FROM org_members WHERE org_id = ? AND user_id = ?",
            (org_id, user_id),
        ) as cur:
            n = cur.rowcount
        await db.commit()
        return n > 0

    async def list_org_members(self, org_id: str) -> list[OrgMember]:
        db = self._require_db()
        async with db.execute(
            "SELECT org_id, user_id, org_role FROM org_members WHERE org_id = ?",
            (org_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [
            OrgMember(org_id=r[0], user_id=r[1], org_role=OrgRole(r[2]))
            for r in rows
        ]

    async def _member_role(self, org_id: str, user_id: str) -> OrgRole | None:
        db = self._require_db()
        async with db.execute(
            "SELECT org_role FROM org_members WHERE org_id = ? AND user_id = ?",
            (org_id, user_id),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return OrgRole(row[0])


class ControlPlaneJobMetaOps:
    """Mixin: record who launched dashboard jobs for list/steer authz."""

    if TYPE_CHECKING:

        def _require_db(self) -> aiosqlite.Connection: ...

    async def record_job_launch(
        self,
        job_id: str,
        *,
        launched_by: str,
        org_id: str | None = None,
    ) -> None:
        db = self._require_db()
        await db.execute(
            "INSERT INTO dash_job_launches (job_id, launched_by, org_id, created_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(job_id) DO UPDATE SET "
            "launched_by = excluded.launched_by, org_id = excluded.org_id",
            (job_id, launched_by, org_id, _utc_now().isoformat()),
        )
        await db.commit()

    async def get_job_meta(
        self, job_id: str
    ) -> tuple[str | None, str | None]:
        """Return (launched_by, org_id) or (None, None)."""
        db = self._require_db()
        async with db.execute(
            "SELECT launched_by, org_id FROM dash_job_launches WHERE job_id = ?",
            (job_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None, None
        return row[0], row[1]

    async def job_meta_map(
        self, job_ids: list[str]
    ) -> dict[str, tuple[str | None, str | None]]:
        if not job_ids:
            return {}
        db = self._require_db()
        placeholders = ",".join("?" * len(job_ids))
        async with db.execute(
            f"SELECT job_id, launched_by, org_id FROM dash_job_launches "
            f"WHERE job_id IN ({placeholders})",
            tuple(job_ids),
        ) as cur:
            rows = await cur.fetchall()
        return {r[0]: (r[1], r[2]) for r in rows}
