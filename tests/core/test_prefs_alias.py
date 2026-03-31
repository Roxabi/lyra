"""Tests for PrefsStore alias-awareness — cross-platform preference lookups (#472)."""

from __future__ import annotations

from pathlib import Path

import pytest

from lyra.core.stores.identity_alias_store import IdentityAliasStore
from lyra.core.stores.prefs_store import PrefsStore, UserPrefs


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def prefs_store(tmp_path: Path):
    store = PrefsStore(db_path=tmp_path / "prefs.db")
    await store.connect()
    try:
        yield store
    finally:
        await store.close()


@pytest.fixture
async def alias_store(tmp_path: Path):
    store = IdentityAliasStore(db_path=tmp_path / "aliases.db")
    await store.connect()
    try:
        yield store
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# Cross-platform preference visibility
# ---------------------------------------------------------------------------


class TestPrefsAliasVisibility:
    @pytest.mark.asyncio
    async def test_prefs_visible_across_aliases(
        self,
        prefs_store: PrefsStore,
        alias_store: IdentityAliasStore,
    ) -> None:
        """Prefs set for tg:user:1 are visible when queried as dc:user:2 after linking."""
        await prefs_store.set_pref("tg:user:1", "tts_language", "fr")
        await alias_store.link("tg:user:1", "dc:user:2")
        prefs_store.set_alias_store(alias_store)

        prefs = await prefs_store.get_prefs("dc:user:2")
        assert prefs.tts_language == "fr"

    @pytest.mark.asyncio
    async def test_set_pref_writes_to_requesting_id(
        self,
        prefs_store: PrefsStore,
        alias_store: IdentityAliasStore,
        tmp_path: Path,
    ) -> None:
        """set_pref always writes to the given user_id — not to any alias."""
        await alias_store.link("tg:user:1", "dc:user:2")
        prefs_store.set_alias_store(alias_store)

        await prefs_store.set_pref("dc:user:2", "tts_voice", "nova")

        # Verify directly in DB: only dc:user:2 has the row
        db = prefs_store._require_db()
        async with db.execute(
            "SELECT user_id, value FROM user_prefs WHERE key='tts_voice'"
        ) as cur:
            rows = await cur.fetchall()
        user_ids_with_pref = {row[0] for row in rows}
        assert user_ids_with_pref == {"dc:user:2"}
        assert "tg:user:1" not in user_ids_with_pref

    @pytest.mark.asyncio
    async def test_no_alias_returns_own_prefs(
        self, prefs_store: PrefsStore
    ) -> None:
        """Without alias_store, get_prefs works normally for the requesting ID."""
        await prefs_store.set_pref("tg:user:1", "tts_language", "de")
        prefs = await prefs_store.get_prefs("tg:user:1")
        assert prefs.tts_language == "de"

    @pytest.mark.asyncio
    async def test_returned_user_id_is_requesting(
        self,
        prefs_store: PrefsStore,
        alias_store: IdentityAliasStore,
    ) -> None:
        """get_prefs(dc:user:2).user_id is always 'dc:user:2', even when prefs came from alias."""
        await prefs_store.set_pref("tg:user:1", "tts_voice", "echo")
        await alias_store.link("tg:user:1", "dc:user:2")
        prefs_store.set_alias_store(alias_store)

        prefs = await prefs_store.get_prefs("dc:user:2")
        assert prefs.user_id == "dc:user:2"
        # And the value was found across the alias
        assert prefs.tts_voice == "echo"

    @pytest.mark.asyncio
    async def test_prefs_defaults_when_no_prefs_exist(
        self,
        prefs_store: PrefsStore,
        alias_store: IdentityAliasStore,
    ) -> None:
        """When no prefs exist for any alias, defaults are returned."""
        await alias_store.link("tg:user:1", "dc:user:2")
        prefs_store.set_alias_store(alias_store)

        prefs = await prefs_store.get_prefs("dc:user:2")
        assert prefs.tts_language == "detected"
        assert prefs.tts_voice == "agent_default"
        assert prefs.user_id == "dc:user:2"

    @pytest.mark.asyncio
    async def test_both_aliases_have_prefs_first_wins(
        self,
        prefs_store: PrefsStore,
        alias_store: IdentityAliasStore,
    ) -> None:
        """When multiple aliases have prefs, the first non-default value wins."""
        await prefs_store.set_pref("tg:user:1", "tts_language", "es")
        await prefs_store.set_pref("dc:user:2", "tts_language", "ja")
        await alias_store.link("tg:user:1", "dc:user:2")
        prefs_store.set_alias_store(alias_store)

        prefs = await prefs_store.get_prefs("dc:user:2")
        # Either value is acceptable — both are non-default; first-read wins
        assert prefs.tts_language in {"es", "ja"}
