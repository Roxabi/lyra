"""Tests for PrefsStore — per-user preference storage (S4, issue #42)."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from lyra.infrastructure.stores.prefs_store import PrefsStore, UserPrefs

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def prefs_store(tmp_path: Path) -> AsyncGenerator[PrefsStore, None]:
    store = PrefsStore(db_path=tmp_path / "prefs.db")
    await store.connect()
    try:
        yield store
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# T15 — connect() creates the table
# ---------------------------------------------------------------------------


class TestPrefsStoreConnect:
    @pytest.mark.asyncio
    async def test_connect_creates_user_prefs_table(self, tmp_path):
        """connect() must CREATE TABLE IF NOT EXISTS user_prefs."""
        store = PrefsStore(db_path=tmp_path / "prefs.db")
        await store.connect()
        assert store._db is not None
        async with store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='user_prefs'"
        ) as cur:
            row = await cur.fetchone()
        assert row is not None, "user_prefs table must be created on connect()"
        await store.close()

    @pytest.mark.asyncio
    async def test_connect_idempotent(self, tmp_path):
        """Calling connect() twice must not raise."""
        store = PrefsStore(db_path=tmp_path / "prefs.db")
        await store.connect()
        await store.connect()  # second call must not raise
        await store.close()


# ---------------------------------------------------------------------------
# T16 — get_prefs returns defaults for unknown users
# ---------------------------------------------------------------------------


class TestPrefsStoreGetPrefs:
    @pytest.mark.asyncio
    async def test_unknown_user_returns_defaults(self, prefs_store):
        """An unknown user_id returns UserPrefs with sentinel defaults."""
        prefs = await prefs_store.get_prefs("tg:user:99999")
        assert isinstance(prefs, UserPrefs)
        assert prefs.tts_language == "detected"
        assert prefs.tts_voice == "agent_default"

    @pytest.mark.asyncio
    async def test_known_user_returns_set_values(self, prefs_store):
        """After set_pref, get_prefs returns the saved value."""
        await prefs_store.set_pref("tg:user:1", "tts_language", "fr")
        prefs = await prefs_store.get_prefs("tg:user:1")
        assert prefs.tts_language == "fr"
        assert prefs.tts_voice == "agent_default"  # unset → default


# ---------------------------------------------------------------------------
# T17 — set_pref persists across reconnect + upsert
# ---------------------------------------------------------------------------


class TestPrefsStoreSetPref:
    @pytest.mark.asyncio
    async def test_set_pref_persists_across_reconnect(self, tmp_path):
        """Values written by set_pref survive a close()+connect() cycle."""
        store1 = PrefsStore(db_path=tmp_path / "prefs.db")
        await store1.connect()
        await store1.set_pref("tg:user:1", "tts_language", "fr")
        await store1.close()

        store2 = PrefsStore(db_path=tmp_path / "prefs.db")
        await store2.connect()
        prefs = await store2.get_prefs("tg:user:1")
        assert prefs.tts_language == "fr"
        await store2.close()

    @pytest.mark.asyncio
    async def test_set_pref_upsert(self, prefs_store):
        """set_pref called twice updates the value (upsert, not duplicate insert)."""
        await prefs_store.set_pref("tg:user:1", "tts_language", "fr")
        await prefs_store.set_pref("tg:user:1", "tts_language", "en")
        prefs = await prefs_store.get_prefs("tg:user:1")
        assert prefs.tts_language == "en"


# ---------------------------------------------------------------------------
# UserPrefs dataclass defaults
# ---------------------------------------------------------------------------


class TestUserPrefsDataclass:
    def test_defaults(self):
        """UserPrefs has sentinel string defaults for language and voice."""
        prefs = UserPrefs(user_id="tg:user:1")
        assert prefs.tts_language == "detected"
        assert prefs.tts_voice == "agent_default"
