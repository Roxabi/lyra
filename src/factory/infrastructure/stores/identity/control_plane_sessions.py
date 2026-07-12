"""Sessions + API keys for ControlPlaneStore."""

from __future__ import annotations

import secrets
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from factory.core.auth.control_plane import (
    ApiKeyRecord,
    AuthVia,
    ControlPlanePrincipal,
    ControlPlaneUser,
    SessionRecord,
    UserStatus,
)
from factory.infrastructure.stores.identity.control_plane_ddl import (
    API_KEY_PREFIX,
    _parse_ts,
    _utc_now,
)
from factory.infrastructure.stores.identity.password_hash import hash_token

if TYPE_CHECKING:
    import aiosqlite

# Default session lifetime (14 days) — product default, not runtime-config V1.
_DEFAULT_SESSION_TTL_SECONDS = 60 * 60 * 24 * 14  # const-ok: session TTL 14d


class ControlPlaneSessionOps:
    """Mixin: dash_sessions + dash_api_keys."""

    if TYPE_CHECKING:

        def _require_db(self) -> aiosqlite.Connection: ...

        async def get_user(self, user_id: str) -> ControlPlaneUser | None: ...

    async def create_session(
        self,
        user_id: str,
        *,
        ttl_seconds: int = _DEFAULT_SESSION_TTL_SECONDS,
    ) -> tuple[SessionRecord, str]:
        token = secrets.token_urlsafe(32)
        sid = f"ses:{uuid4().hex}"
        now = _utc_now()
        expires = now + timedelta(seconds=ttl_seconds)
        db = self._require_db()
        await db.execute(
            "INSERT INTO dash_sessions "
            "(id, user_id, token_hash, expires_at, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (sid, user_id, hash_token(token), expires.isoformat(), now.isoformat()),
        )
        await db.commit()
        rec = SessionRecord(
            id=sid, user_id=user_id, expires_at=expires, created_at=now
        )
        return rec, token

    async def resolve_session(self, raw_token: str) -> ControlPlanePrincipal | None:
        if not raw_token:
            return None
        db = self._require_db()
        th = hash_token(raw_token)
        async with db.execute(
            "SELECT id, user_id, expires_at FROM dash_sessions WHERE token_hash = ?",
            (th,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        _sid, user_id, expires_at_s = row
        if _utc_now() > _parse_ts(expires_at_s):
            await db.execute("DELETE FROM dash_sessions WHERE token_hash = ?", (th,))
            await db.commit()
            return None
        user = await self.get_user(user_id)
        if user is None or user.status != UserStatus.ACTIVE:
            return None
        return await self.principal_for_user(user, via="session")

    async def revoke_session(self, raw_token: str) -> bool:
        if not raw_token:
            return False
        db = self._require_db()
        async with db.execute(
            "DELETE FROM dash_sessions WHERE token_hash = ?",
            (hash_token(raw_token),),
        ) as cur:
            n = cur.rowcount
        await db.commit()
        return n > 0

    async def revoke_user_sessions(self, user_id: str) -> int:
        db = self._require_db()
        async with db.execute(
            "DELETE FROM dash_sessions WHERE user_id = ?",
            (user_id,),
        ) as cur:
            n = cur.rowcount
        await db.commit()
        return n

    async def create_api_key(
        self,
        user_id: str,
        *,
        name: str,
    ) -> tuple[ApiKeyRecord, str]:
        label = name.strip() or "default"
        raw = f"{API_KEY_PREFIX}{secrets.token_urlsafe(24)}"
        prefix = raw[:12]
        kid = f"key:{uuid4().hex}"
        now = _utc_now()
        db = self._require_db()
        await db.execute(
            "INSERT INTO dash_api_keys "
            "(id, user_id, name, key_hash, prefix, scopes, created_at, revoked_at) "
            "VALUES (?, ?, ?, ?, ?, NULL, ?, NULL)",
            (kid, user_id, label, hash_token(raw), prefix, now.isoformat()),
        )
        await db.commit()
        rec = ApiKeyRecord(
            id=kid,
            user_id=user_id,
            name=label,
            prefix=prefix,
            created_at=now,
            revoked_at=None,
        )
        return rec, raw

    async def list_api_keys(self, user_id: str) -> list[ApiKeyRecord]:
        db = self._require_db()
        async with db.execute(
            "SELECT id, user_id, name, prefix, created_at, revoked_at "
            "FROM dash_api_keys WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [
            ApiKeyRecord(
                id=r[0],
                user_id=r[1],
                name=r[2],
                prefix=r[3],
                created_at=_parse_ts(r[4]),
                revoked_at=_parse_ts(r[5]) if r[5] else None,
            )
            for r in rows
        ]

    async def revoke_api_key(
        self, key_id: str, *, user_id: str | None = None
    ) -> bool:
        db = self._require_db()
        now = _utc_now().isoformat()
        if user_id is None:
            q = (
                "UPDATE dash_api_keys SET revoked_at = ? "
                "WHERE id = ? AND revoked_at IS NULL"
            )
            params: tuple = (now, key_id)
        else:
            q = (
                "UPDATE dash_api_keys SET revoked_at = ? "
                "WHERE id = ? AND user_id = ? AND revoked_at IS NULL"
            )
            params = (now, key_id, user_id)
        async with db.execute(q, params) as cur:
            n = cur.rowcount
        await db.commit()
        return n > 0

    async def resolve_api_key(self, raw_key: str) -> ControlPlanePrincipal | None:
        if not raw_key:
            return None
        db = self._require_db()
        async with db.execute(
            "SELECT user_id, revoked_at FROM dash_api_keys WHERE key_hash = ?",
            (hash_token(raw_key),),
        ) as cur:
            row = await cur.fetchone()
        if row is None or row[1] is not None:
            return None
        user = await self.get_user(row[0])
        if user is None or user.status != UserStatus.ACTIVE:
            return None
        return await self.principal_for_user(user, via="api_key")

    async def principal_for_user(
        self,
        user: ControlPlaneUser,
        *,
        via: str,
        active_org_id: str | None = None,
    ) -> ControlPlanePrincipal:
        via_t: AuthVia
        if via in ("session", "api_key", "platform_link", "sys", "e2e"):
            via_t = via  # type: ignore[assignment]
        else:
            via_t = "session"
        org_ids = await self.org_ids_for_user(user.id)  # type: ignore[attr-defined]
        active = active_org_id
        if active is not None and active not in org_ids:
            active = None
        return ControlPlanePrincipal(
            user_id=user.id,
            roles=frozenset({user.global_role.value}),
            org_ids=org_ids,
            active_org_id=active,
            via=via_t,
        )
