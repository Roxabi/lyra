"""Dashboard platform link codes + chat_ready (ADR-103 Blocks 8–9)."""

from __future__ import annotations

import logging
import secrets
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from factory.core.auth.platform_keys import parse_platform_key
from factory.infrastructure.stores.identity.control_plane_ddl import (
    _parse_ts,
    _utc_now,
)
from factory.infrastructure.stores.identity.password_hash import hash_token

if TYPE_CHECKING:
    import aiosqlite

log = logging.getLogger(__name__)

_LINK_TTL_SECONDS = 600  # const-ok: 10m dashboard link pairing TTL


class ControlPlaneLinkOps:
    """Mixin: mint/consume link codes; dual-platform chat_ready."""

    if TYPE_CHECKING:

        def _require_db(self) -> aiosqlite.Connection: ...

    async def create_link_code(
        self,
        user_id: str,
        *,
        platform: str | None = None,
        ttl_seconds: int = _LINK_TTL_SECONDS,
    ) -> tuple[str, str]:
        """Mint a one-time link code. Returns (code_id, plaintext_token)."""
        if platform is not None and platform not in ("telegram", "discord"):
            raise ValueError("platform must be telegram, discord, or None")
        token = secrets.token_urlsafe(12)
        code_id = f"lnk:{uuid4().hex}"
        now = _utc_now()
        expires = now + timedelta(seconds=ttl_seconds)
        db = self._require_db()
        await db.execute(
            "INSERT INTO dash_link_codes "
            "(id, user_id, platform, token_hash, expires_at, created_at, consumed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, NULL)",
            (
                code_id,
                user_id,
                platform,
                hash_token(token),
                expires.isoformat(),
                now.isoformat(),
            ),
        )
        await db.commit()
        return code_id, token

    async def consume_link_code(
        self,
        raw_token: str,
        *,
        platform_key: str,
    ) -> str:
        """Validate code and attach *platform_key* → dash user. Returns user_id."""
        parsed = parse_platform_key(platform_key)
        if parsed is None:
            raise ValueError(f"invalid platform key: {platform_key!r}")
        platform, platform_uid, canonical = parsed

        db = self._require_db()
        th = hash_token(raw_token)
        async with db.execute(
            "SELECT id, user_id, platform, expires_at, consumed_at "
            "FROM dash_link_codes WHERE token_hash = ?",
            (th,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise ValueError("invalid or expired link code")
        code_id, user_id, want_platform, expires_at_s, consumed = row
        if consumed is not None:
            raise ValueError("link code already used")
        if _utc_now() > _parse_ts(expires_at_s):
            raise ValueError("link code expired")
        if want_platform is not None and want_platform != platform:
            raise ValueError(f"code is for {want_platform}, not {platform}")

        async with db.execute(
            "SELECT user_id FROM platform_identities WHERE platform_key = ?",
            (canonical,),
        ) as cur:
            existing = await cur.fetchone()
        if existing is not None and existing[0] != user_id:
            raise ValueError(f"platform identity already linked: {canonical}")

        async with db.execute(
            "SELECT platform_key FROM platform_identities "
            "WHERE user_id = ? AND platform = ?",
            (user_id, platform),
        ) as cur:
            old_keys = [r[0] async for r in cur]
        for old in old_keys:
            if old != canonical:
                await db.execute(
                    "DELETE FROM platform_identities WHERE platform_key = ?",
                    (old,),
                )

        if existing is None:
            await db.execute(
                "INSERT INTO platform_identities "
                "(platform_key, platform, platform_uid, user_id) "
                "VALUES (?, ?, ?, ?)",
                (canonical, platform, platform_uid, user_id),
            )
        await db.execute(
            "UPDATE dash_link_codes SET consumed_at = ? WHERE id = ?",
            (_utc_now().isoformat(), code_id),
        )
        await db.commit()
        log.info("Platform link: %s → user %s", canonical, user_id)
        return user_id

    async def list_platform_links(self, user_id: str) -> list[dict[str, str]]:
        db = self._require_db()
        async with db.execute(
            "SELECT platform, platform_key, linked_at FROM platform_identities "
            "WHERE user_id = ? ORDER BY platform",
            (user_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [
            {"platform": r[0], "platform_key": r[1], "linked_at": r[2]}
            for r in rows
        ]

    async def chat_ready(self, user_id: str) -> bool:
        links = await self.list_platform_links(user_id)
        platforms = {row["platform"] for row in links}
        return "telegram" in platforms and "discord" in platforms

    async def unlink_platform(self, user_id: str, platform: str) -> bool:
        if platform not in ("telegram", "discord"):
            raise ValueError("platform must be telegram or discord")
        db = self._require_db()
        async with db.execute(
            "DELETE FROM platform_identities WHERE user_id = ? AND platform = ?",
            (user_id, platform),
        ) as cur:
            n = cur.rowcount
        await db.commit()
        return n > 0

    async def is_platform_chat_ready(self, platform_key: str) -> bool:
        """True when platform key belongs to a dual-linked dash user."""
        if parse_platform_key(platform_key) is None:
            return False
        db = self._require_db()
        async with db.execute(
            "SELECT user_id FROM platform_identities WHERE platform_key = ?",
            (platform_key,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return False
        return await self.chat_ready(row[0])

    async def dash_user_for_platform(self, platform_key: str) -> str | None:
        db = self._require_db()
        async with db.execute(
            "SELECT user_id FROM platform_identities WHERE platform_key = ?",
            (platform_key,),
        ) as cur:
            row = await cur.fetchone()
        return row[0] if row else None
