"""User preference store — per-user TTS/STT settings stored in auth.db."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .sqlite_base import SqliteStore

if TYPE_CHECKING:
    from .identity_alias_store import IdentityAliasStore

log = logging.getLogger(__name__)

__all__ = ["PrefsStore", "UserPrefs"]

_CREATE_USER_PREFS = """
CREATE TABLE IF NOT EXISTS user_prefs (
    user_id  TEXT NOT NULL,
    key      TEXT NOT NULL,
    value    TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
)
"""


@dataclass
class UserPrefs:
    """Per-user TTS/STT preferences.

    Sentinel values:
    - tts_language = "detected" → use Whisper-detected language per call
    - tts_voice = "agent_default" → use agent [tts].voice (or voicecli global)
    """

    user_id: str
    tts_language: str = "detected"
    tts_voice: str = "agent_default"


class PrefsStore(SqliteStore):
    """SQLite-backed user preference store (aiosqlite).

    Shares auth.db with AuthStore — each opens its own connection handle,
    which is safe under WAL mode. Manages the user_prefs table only.
    """

    def __init__(self, db_path: str | Path) -> None:
        super().__init__(db_path)
        self._alias_store: IdentityAliasStore | None = None

    def set_alias_store(self, store: IdentityAliasStore) -> None:
        """Wire up the alias store for cross-platform preference lookups."""
        self._alias_store = store

    async def connect(self) -> None:
        """Open aiosqlite, enable WAL, create user_prefs table."""
        await self._open_db(ddl=[_CREATE_USER_PREFS])
        log.info("PrefsStore connected (db=%s)", self._db_path)

    async def get_prefs(self, user_id: str) -> UserPrefs:
        """Return preferences for user_id; sentinel defaults for unknown users.

        When an alias store is wired, resolves all linked platform IDs and
        queries them in a single IN clause. The returned UserPrefs always
        carries the requesting user_id as its identifier.
        """
        db = self._require_db()

        # Resolve aliases
        if self._alias_store is not None:
            aliases = self._alias_store.resolve_aliases(user_id)
        else:
            aliases = frozenset({user_id})

        prefs = UserPrefs(user_id=user_id)  # Always use requesting user_id

        if len(aliases) == 1:
            # Fast path: single ID, use original query
            async with db.execute(
                "SELECT key, value FROM user_prefs WHERE user_id = ?", (user_id,)
            ) as cur:
                async for key, value in cur:
                    if key == "tts_language":
                        prefs.tts_language = value
                    elif key == "tts_voice":
                        prefs.tts_voice = value
        else:
            # Multi-alias: single IN query; first non-default value wins
            placeholders = ", ".join("?" * len(aliases))
            async with db.execute(
                f"SELECT key, value FROM user_prefs WHERE user_id IN ({placeholders})"
                f" ORDER BY CASE WHEN user_id = ? THEN 0 ELSE 1 END",
                (*tuple(aliases), user_id),
            ) as cur:
                async for key, value in cur:
                    if key == "tts_language" and prefs.tts_language == "detected":
                        prefs.tts_language = value
                    elif key == "tts_voice" and prefs.tts_voice == "agent_default":
                        prefs.tts_voice = value

        return prefs

    async def set_pref(self, user_id: str, key: str, value: str) -> None:
        """Upsert a single preference key for a user."""
        db = self._require_db()
        await db.execute(
            "INSERT INTO user_prefs (user_id, key, value) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value",
            (user_id, key, value),
        )
        await db.commit()

    async def close(self) -> None:
        """Close the database connection."""
        await super().close()
        log.info("PrefsStore closed")
