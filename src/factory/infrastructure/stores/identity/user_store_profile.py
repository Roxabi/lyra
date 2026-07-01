"""Dashboard admin profile + platform identity operations for UserStore."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import uuid4

from factory.core.auth.platform_keys import USER_ID_PREFIX, format_platform_key
from factory.core.auth.user_models import PlatformIdentity, User

if TYPE_CHECKING:
    import aiosqlite

log = logging.getLogger(__name__)

_IDENTITY_COLS = "platform_key, platform, platform_uid, user_id, linked_at"


def _parse_ts(raw: str) -> datetime:
    ts = datetime.fromisoformat(raw)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


class UserStoreProfileOps:
    """Mixin: profile CRUD and platform identity linking for dashboard admin."""

    if TYPE_CHECKING:
        _key_to_user: dict[str, str]
        _user_to_keys: dict[str, set[str]]

        def _require_db(self) -> aiosqlite.Connection: ...

        async def get_user(self, user_id: str) -> User | None: ...

    @staticmethod
    def _normalize_email(email: str) -> str:
        return email.strip().lower()

    async def create_profile_user(
        self,
        *,
        display_name: str,
        email: str,
    ) -> User:
        """Create a dashboard-managed user without a platform identity yet."""
        name = display_name.strip()
        normalized = self._normalize_email(email)
        if not name:
            raise ValueError("display_name must be non-empty")
        if not normalized:
            raise ValueError("email must be non-empty")

        user_id = f"{USER_ID_PREFIX}{uuid4().hex}"
        db = self._require_db()
        try:
            await db.execute(
                "INSERT INTO users (id, display_name, email) VALUES (?, ?, ?)",
                (user_id, name, normalized),
            )
            await db.commit()
        except Exception as exc:
            if "UNIQUE constraint failed" in str(exc):
                raise ValueError(f"email already registered: {normalized}") from exc
            raise

        async with db.execute(
            "SELECT id, display_name, email, created_at FROM users WHERE id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise RuntimeError(f"user {user_id!r} missing after insert")
        uid, stored_name, stored_email, created_at = row
        log.info("Created profile user %s (%s)", uid, stored_email)
        return User(
            id=uid,
            display_name=stored_name,
            email=stored_email,
            created_at=_parse_ts(created_at),
        )

    async def delete_profile_user(self, user_id: str) -> bool:
        """Delete a profile user and linked platform identities (admin rollback)."""
        db = self._require_db()
        async with db.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)) as cur:
            if await cur.fetchone() is None:
                return False

        async with db.execute(
            "SELECT platform_key FROM platform_identities WHERE user_id = ?",
            (user_id,),
        ) as cur:
            platform_keys = [row[0] async for row in cur]

        await db.execute(
            "DELETE FROM platform_identities WHERE user_id = ?",
            (user_id,),
        )
        await db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        await db.commit()

        for key in platform_keys:
            self._key_to_user.pop(key, None)
        self._user_to_keys.pop(user_id, None)
        log.info("Deleted profile user %s", user_id)
        return True

    async def update_profile_user(
        self,
        user_id: str,
        *,
        display_name: str | None = None,
        email: str | None = None,
    ) -> User | None:
        """Update display name and/or email for an existing canonical user."""
        if display_name is None and email is None:
            return await self.get_user(user_id)

        db = self._require_db()
        async with db.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)) as cur:
            if await cur.fetchone() is None:
                return None

        sets: list[str] = []
        params: list[str] = []
        if display_name is not None:
            name = display_name.strip()
            if not name:
                raise ValueError("display_name must be non-empty")
            sets.append("display_name = ?")
            params.append(name)
        if email is not None:
            normalized = self._normalize_email(email)
            if not normalized:
                raise ValueError("email must be non-empty")
            sets.append("email = ?")
            params.append(normalized)

        params.append(user_id)
        try:
            await db.execute(
                f"UPDATE users SET {', '.join(sets)} WHERE id = ?",
                tuple(params),
            )
            await db.commit()
        except Exception as exc:
            if "UNIQUE constraint failed" in str(exc):
                raise ValueError("email already registered") from exc
            raise

        return await self.get_user(user_id)

    async def set_platform_identity(
        self,
        user_id: str,
        platform: str,
        platform_uid: str | None,
    ) -> None:
        """Attach, replace, or clear a platform identity for a canonical user."""
        db = self._require_db()
        async with db.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)) as cur:
            if await cur.fetchone() is None:
                raise ValueError(f"user not found: {user_id}")

        uid = (platform_uid or "").strip()
        query = (
            "SELECT platform_key FROM platform_identities "
            "WHERE user_id = ? AND platform = ?"
        )
        async with db.execute(query, (user_id, platform)) as cur:
            old_keys = [row[0] async for row in cur]

        if not uid:
            if not old_keys:
                return
            await db.execute(
                "DELETE FROM platform_identities WHERE user_id = ? AND platform = ?",
                (user_id, platform),
            )
            await db.commit()
            for key in old_keys:
                self._key_to_user.pop(key, None)
                self._user_to_keys.get(user_id, set()).discard(key)
            return

        platform_key = format_platform_key(platform, uid)
        existing_user = self._key_to_user.get(platform_key)
        if existing_user is not None and existing_user != user_id:
            raise ValueError(f"platform identity already linked: {platform_key}")

        keys_to_remove = [key for key in old_keys if key != platform_key]
        if keys_to_remove:
            await db.execute(
                "DELETE FROM platform_identities "
                "WHERE user_id = ? AND platform = ? AND platform_key != ?",
                (user_id, platform, platform_key),
            )
            for key in keys_to_remove:
                self._key_to_user.pop(key, None)
                self._user_to_keys.get(user_id, set()).discard(key)

        if platform_key not in self._key_to_user:
            await db.execute(
                "INSERT INTO platform_identities "
                "(platform_key, platform, platform_uid, user_id) "
                "VALUES (?, ?, ?, ?)",
                (platform_key, platform, uid, user_id),
            )
            await db.commit()
            self._key_to_user[platform_key] = user_id
            self._user_to_keys.setdefault(user_id, set()).add(platform_key)
        elif keys_to_remove:
            await db.commit()

    async def list_platform_identities(
        self, user_id: str
    ) -> tuple[PlatformIdentity, ...]:
        db = self._require_db()
        identities: list[PlatformIdentity] = []
        async with db.execute(
            f"SELECT {_IDENTITY_COLS} FROM platform_identities WHERE user_id = ?",
            (user_id,),
        ) as cur:
            async for row in cur:
                platform_key, platform, platform_uid, uid, linked_at = row
                identities.append(
                    PlatformIdentity(
                        platform_key=platform_key,
                        platform=platform,
                        platform_uid=platform_uid,
                        user_id=uid,
                        linked_at=_parse_ts(linked_at),
                    )
                )
        return tuple(identities)

    async def list_users(self) -> tuple[User, ...]:
        db = self._require_db()
        users: list[User] = []
        async with db.execute(
            "SELECT id, display_name, email, created_at FROM users ORDER BY created_at"
        ) as cur:
            async for row in cur:
                uid, display_name, email, created_at = row
                users.append(
                    User(
                        id=uid,
                        display_name=display_name,
                        email=email,
                        created_at=_parse_ts(created_at),
                    )
                )
        return tuple(users)