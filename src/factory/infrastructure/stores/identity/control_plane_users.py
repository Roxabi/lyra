"""User CRUD + bootstrap for ControlPlaneStore."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from factory.core.auth.control_plane import (
    ControlPlaneUser,
    GlobalRole,
    UserStatus,
)
from factory.core.auth.platform_keys import USER_ID_PREFIX
from factory.infrastructure.stores.identity.control_plane_ddl import (
    _normalize_email,
    _parse_ts,
)
from factory.infrastructure.stores.identity.password_hash import (
    hash_password,
    verify_password,
)

if TYPE_CHECKING:
    import aiosqlite

log = logging.getLogger(__name__)

_USER_COLS = "id, display_name, email, created_at, password_hash, global_role, status"


def _new_user_id() -> str:
    return f"{USER_ID_PREFIX}{uuid4().hex}"


class ControlPlaneUserOps:
    """Mixin: dashboard user directory operations."""

    if TYPE_CHECKING:

        def _require_db(self) -> aiosqlite.Connection: ...

        async def revoke_user_sessions(self, user_id: str) -> int: ...

    def _row_user(self, row: Any) -> ControlPlaneUser:
        uid, display_name, email, created_at, password_hash, global_role, status = row
        return ControlPlaneUser(
            id=uid,
            email=email,
            display_name=display_name,
            global_role=GlobalRole(global_role or GlobalRole.MEMBER.value),
            status=UserStatus(status or UserStatus.ACTIVE.value),
            created_at=_parse_ts(created_at),
            has_password=bool(password_hash),
        )

    async def get_user(self, user_id: str) -> ControlPlaneUser | None:
        db = self._require_db()
        async with db.execute(
            f"SELECT {_USER_COLS} FROM users WHERE id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        return self._row_user(row) if row else None

    async def get_user_by_email(self, email: str) -> ControlPlaneUser | None:
        db = self._require_db()
        normalized = _normalize_email(email)
        async with db.execute(
            f"SELECT {_USER_COLS} FROM users WHERE email = ?",
            (normalized,),
        ) as cur:
            row = await cur.fetchone()
        return self._row_user(row) if row else None

    async def list_users(self) -> list[ControlPlaneUser]:
        db = self._require_db()
        async with db.execute(
            f"SELECT {_USER_COLS} FROM users ORDER BY created_at"
        ) as cur:
            rows = await cur.fetchall()
        return [self._row_user(r) for r in rows]

    async def create_user(
        self,
        *,
        email: str,
        password: str,
        global_role: GlobalRole = GlobalRole.MEMBER,
        display_name: str | None = None,
        user_id: str | None = None,
    ) -> ControlPlaneUser:
        normalized = _normalize_email(email)
        if not normalized:
            raise ValueError("email must be non-empty")
        uid = user_id or _new_user_id()
        pw_hash = hash_password(password)
        name = (display_name or normalized.split("@", 1)[0]).strip()
        db = self._require_db()
        try:
            await db.execute(
                "INSERT INTO users "
                "(id, display_name, email, password_hash, global_role, status) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    uid,
                    name,
                    normalized,
                    pw_hash,
                    global_role.value,
                    UserStatus.ACTIVE.value,
                ),
            )
            await db.commit()
        except Exception as exc:
            if "UNIQUE constraint failed" in str(exc):
                raise ValueError(f"email already registered: {normalized}") from exc
            raise
        user = await self.get_user(uid)
        if user is None:
            raise RuntimeError(f"user {uid!r} missing after insert")
        log.info("Created control-plane user %s role=%s", uid, global_role.value)
        return user

    async def set_password(self, user_id: str, password: str) -> None:
        db = self._require_db()
        await db.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hash_password(password), user_id),
        )
        await db.commit()

    async def verify_password(
        self, email: str, password: str
    ) -> ControlPlaneUser | None:
        user = await self.get_user_by_email(email)
        if user is None or user.status != UserStatus.ACTIVE:
            return None
        db = self._require_db()
        async with db.execute(
            "SELECT password_hash FROM users WHERE id = ?",
            (user.id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None or not row[0]:
            return None
        if not verify_password(password, row[0]):
            return None
        return user

    async def set_user_status(self, user_id: str, status: str) -> None:
        db = self._require_db()
        await db.execute(
            "UPDATE users SET status = ? WHERE id = ?",
            (status, user_id),
        )
        await db.commit()
        if status == UserStatus.DISABLED.value:
            await self.revoke_user_sessions(user_id)

    async def bootstrap_admin_if_empty(
        self,
        *,
        email: str,
        password: str,
        display_name: str = "Admin",
    ) -> ControlPlaneUser | None:
        db = self._require_db()
        async with db.execute(
            "SELECT 1 FROM users WHERE global_role = ? AND status = ? LIMIT 1",
            (GlobalRole.ADMIN.value, UserStatus.ACTIVE.value),
        ) as cur:
            if await cur.fetchone() is not None:
                return None
        existing = await self.get_user_by_email(email)
        if existing is not None:
            await db.execute(
                "UPDATE users SET global_role = ?, status = ?, password_hash = ?, "
                "display_name = COALESCE(display_name, ?) WHERE id = ?",
                (
                    GlobalRole.ADMIN.value,
                    UserStatus.ACTIVE.value,
                    hash_password(password),
                    display_name,
                    existing.id,
                ),
            )
            await db.commit()
            log.warning("Bootstrap: promoted existing user %s to admin", existing.id)
            return await self.get_user(existing.id)
        user = await self.create_user(
            email=email,
            password=password,
            global_role=GlobalRole.ADMIN,
            display_name=display_name,
        )
        log.warning("Bootstrap: created admin user %s (%s)", user.id, email)
        return user
