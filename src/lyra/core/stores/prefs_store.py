"""User preference store — per-user TTS/STT settings stored in auth.db."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .sqlite_base import SqliteStore

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

    async def connect(self) -> None:
        """Open aiosqlite, enable WAL, create user_prefs table."""
        await self._open_db(ddl=[_CREATE_USER_PREFS])
        log.info("PrefsStore connected (db=%s)", self._db_path)

    async def get_prefs(self, user_id: str) -> UserPrefs:
        """Return preferences for user_id; sentinel defaults for unknown users."""
        db = self._require_db()
        prefs = UserPrefs(user_id=user_id)
        async with db.execute(
            "SELECT key, value FROM user_prefs WHERE user_id = ?", (user_id,)
        ) as cur:
            async for key, value in cur:
                if key == "tts_language":
                    prefs.tts_language = value
                elif key == "tts_voice":
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
