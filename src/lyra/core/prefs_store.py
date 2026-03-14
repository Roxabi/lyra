"""User preference store — per-user TTS/STT settings stored in auth.db."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

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


class PrefsStore:
    """SQLite-backed user preference store (aiosqlite).

    Shares auth.db with AuthStore — each opens its own connection handle,
    which is safe under WAL mode. Manages the user_prefs table only.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._db: aiosqlite.Connection | None = None

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("call connect() first")
        return self._db

    async def connect(self) -> None:
        """Open aiosqlite, enable WAL, create user_prefs table."""
        if self._db is not None:
            return  # idempotent
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute(_CREATE_USER_PREFS)
        await self._db.commit()
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
        if self._db is not None:
            await self._db.close()
            self._db = None
            log.info("PrefsStore closed")
