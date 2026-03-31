"""Tests for IdentityAliasStore — link, resolve, challenge lifecycle (#472)."""

from __future__ import annotations

from pathlib import Path

import pytest

from lyra.core.stores.identity_alias_store import IdentityAliasStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def alias_store(tmp_path: Path):
    store = IdentityAliasStore(db_path=tmp_path / "aliases.db")
    await store.connect()
    try:
        yield store
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_connect_close_lifecycle(self, tmp_path: Path) -> None:
        """connect() creates tables; close() tears down cleanly."""
        store = IdentityAliasStore(db_path=tmp_path / "aliases.db")
        await store.connect()
        db = store._require_db()
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
            " AND name IN ('identity_aliases', 'link_challenges')"
        ) as cur:
            rows = await cur.fetchall()
        table_names = {r[0] for r in rows}
        assert "identity_aliases" in table_names
        assert "link_challenges" in table_names
        await store.close()


# ---------------------------------------------------------------------------
# Alias resolution
# ---------------------------------------------------------------------------


class TestAliasResolution:
    @pytest.mark.asyncio
    async def test_link_and_resolve_from_secondary(self, alias_store) -> None:
        """After link(primary, secondary), resolve_aliases(secondary) returns both."""
        await alias_store.link("tg:user:1", "dc:user:2")
        result = alias_store.resolve_aliases("dc:user:2")
        assert result == frozenset({"tg:user:1", "dc:user:2"})

    @pytest.mark.asyncio
    async def test_resolve_from_primary(self, alias_store) -> None:
        """resolve_aliases(primary) returns both IDs via _reverse lookup."""
        await alias_store.link("tg:user:1", "dc:user:2")
        result = alias_store.resolve_aliases("tg:user:1")
        assert result == frozenset({"tg:user:1", "dc:user:2"})

    @pytest.mark.asyncio
    async def test_resolve_no_alias(self, alias_store) -> None:
        """An unlinked ID resolves to a singleton frozenset of itself."""
        result = alias_store.resolve_aliases("tg:user:99")
        assert result == frozenset({"tg:user:99"})

    @pytest.mark.asyncio
    async def test_link_is_flat_n1(self, alias_store) -> None:
        """Multiple secondaries under the same primary all appear in any resolve."""
        await alias_store.link("tg:user:1", "dc:user:2")
        await alias_store.link("tg:user:1", "matrix:user:3")

        # Resolving any of the three IDs should return all three
        expected = frozenset({"tg:user:1", "dc:user:2", "matrix:user:3"})
        assert alias_store.resolve_aliases("tg:user:1") == expected
        assert alias_store.resolve_aliases("dc:user:2") == expected
        assert alias_store.resolve_aliases("matrix:user:3") == expected


# ---------------------------------------------------------------------------
# Unlink
# ---------------------------------------------------------------------------


class TestUnlink:
    @pytest.mark.asyncio
    async def test_unlink_removes_alias(self, alias_store) -> None:
        """After unlink, resolve returns a singleton."""
        await alias_store.link("tg:user:1", "dc:user:2")
        removed = await alias_store.unlink("dc:user:2")
        assert removed is True
        result = alias_store.resolve_aliases("dc:user:2")
        assert result == frozenset({"dc:user:2"})

    @pytest.mark.asyncio
    async def test_unlink_updates_both_caches(self, alias_store) -> None:
        """After unlink, both _cache and _reverse are clean for the removed ID."""
        await alias_store.link("tg:user:1", "dc:user:2")
        await alias_store.unlink("dc:user:2")

        # _cache must not contain the secondary anymore
        assert "dc:user:2" not in alias_store._cache
        # _reverse for the primary must not contain the secondary anymore
        siblings = alias_store._reverse.get("tg:user:1", set())
        assert "dc:user:2" not in siblings

    @pytest.mark.asyncio
    async def test_unlink_nonexistent_returns_false(self, alias_store) -> None:
        """unlink on an unknown ID returns False without error."""
        removed = await alias_store.unlink("nobody:1")
        assert removed is False


# ---------------------------------------------------------------------------
# Challenge codes
# ---------------------------------------------------------------------------


class TestChallenges:
    @pytest.mark.asyncio
    async def test_create_challenge_and_validate(self, alias_store) -> None:
        """create_challenge returns a 6-char code; validate returns success."""
        code = await alias_store.create_challenge(
            initiator_id="tg:user:1", platform="telegram"
        )
        assert len(code) == 6
        valid, initiator_id, platform = await alias_store.validate_challenge(code)
        assert valid is True
        assert initiator_id == "tg:user:1"
        assert platform == "telegram"

    @pytest.mark.asyncio
    async def test_validate_expired_challenge(self, alias_store) -> None:
        """A challenge with ttl=0 is already expired on first validation."""
        code = await alias_store.create_challenge(
            initiator_id="tg:user:1", platform="telegram", ttl_seconds=0
        )
        valid, initiator_id, platform = await alias_store.validate_challenge(code)
        assert valid is False
        assert initiator_id == ""
        assert platform == ""

    @pytest.mark.asyncio
    async def test_validate_wrong_code(self, alias_store) -> None:
        """validate_challenge with a wrong code returns (False, '', '')."""
        await alias_store.create_challenge(
            initiator_id="tg:user:1", platform="telegram"
        )
        valid, initiator_id, platform = await alias_store.validate_challenge("XXXXXX")
        assert valid is False
        assert initiator_id == ""
        assert platform == ""

    @pytest.mark.asyncio
    async def test_validate_challenge_is_single_use(self, alias_store) -> None:
        """A valid code cannot be reused — second validation fails."""
        code = await alias_store.create_challenge(
            initiator_id="tg:user:1", platform="telegram"
        )
        valid1, _, _ = await alias_store.validate_challenge(code)
        valid2, _, _ = await alias_store.validate_challenge(code)
        assert valid1 is True
        assert valid2 is False
