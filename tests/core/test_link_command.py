"""Tests for /link and /unlink identity command handlers (#472)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lyra.commands.identity.handlers import cmd_link, cmd_unlink
from lyra.core.message import InboundMessage
from lyra.core.pool import Pool
from lyra.core.stores.identity_alias_store import IdentityAliasStore
from lyra.core.trust import TrustLevel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_msg(
    user_id: str = "tg:user:1",
    platform: str = "telegram",
    *,
    is_admin: bool = True,
) -> InboundMessage:
    return InboundMessage(
        id="msg-1",
        platform=platform,
        bot_id="main",
        scope_id="chat:42",
        user_id=user_id,
        user_name="Tester",
        is_mention=False,
        text="",
        text_raw="",
        timestamp=datetime.now(timezone.utc),
        platform_meta={
            "chat_id": 42,
            "topic_id": None,
            "message_id": None,
            "is_group": False,
        },
        trust_level=TrustLevel.TRUSTED,
        is_admin=is_admin,
    )


def _make_pool(alias_store: IdentityAliasStore | None) -> Pool:
    """Build a Pool whose _ctx exposes the given alias_store."""
    ctx = MagicMock()
    ctx._alias_store = alias_store
    # No authenticators needed for most tests
    ctx._authenticators = {}
    pool = Pool(pool_id="test", agent_name="test", ctx=ctx)
    return pool


# ---------------------------------------------------------------------------
# /link — initiate (no args)
# ---------------------------------------------------------------------------


class TestLinkInitiate:
    @pytest.mark.asyncio
    async def test_link_initiate_returns_code(self, tmp_path: Path) -> None:
        """/link with no args generates and returns a 6-char code."""
        store = IdentityAliasStore(db_path=tmp_path / "aliases.db")
        await store.connect()
        try:
            pool = _make_pool(store)
            msg = _make_msg(user_id="tg:user:1", platform="telegram", is_admin=True)
            response = await cmd_link(msg, pool, [])
            # Response must contain the 6-char code somewhere
            assert "/link " in response.content
            # Extract code — last token of the `/link <code>` instruction
            lines = response.content.split()
            code_candidates = [t for t in lines if len(t) == 6 and t.isalnum()]
            assert len(code_candidates) >= 1
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_link_non_admin_rejected(self, tmp_path: Path) -> None:
        """/link is admin-only — non-admin receives an error."""
        store = IdentityAliasStore(db_path=tmp_path / "aliases.db")
        await store.connect()
        try:
            pool = _make_pool(store)
            msg = _make_msg(is_admin=False)
            response = await cmd_link(msg, pool, [])
            assert "admin" in response.content.lower()
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_link_no_alias_store(self) -> None:
        """When alias_store is None, /link returns 'not available'."""
        pool = _make_pool(alias_store=None)
        msg = _make_msg(is_admin=True)
        response = await cmd_link(msg, pool, [])
        assert "not available" in response.content.lower()


# ---------------------------------------------------------------------------
# /link — complete (with code arg)
# ---------------------------------------------------------------------------


class TestLinkComplete:
    @pytest.mark.asyncio
    async def test_link_complete_creates_alias(self, tmp_path: Path) -> None:
        """Completing a valid challenge from a different platform creates the alias."""
        store = IdentityAliasStore(db_path=tmp_path / "aliases.db")
        await store.connect()
        try:
            # Initiate from telegram
            code = await store.create_challenge(
                initiator_id="tg:user:1", platform="telegram"
            )
            pool = _make_pool(store)
            # Complete from discord
            msg = _make_msg(user_id="dc:user:2", platform="discord", is_admin=True)
            response = await cmd_link(msg, pool, [code])
            assert "linked" in response.content.lower()

            # Alias must exist
            aliases = store.resolve_aliases("dc:user:2")
            assert "tg:user:1" in aliases
            assert "dc:user:2" in aliases
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_link_same_platform_rejected(self, tmp_path: Path) -> None:
        """Completing a challenge from the same platform as initiation is rejected."""
        store = IdentityAliasStore(db_path=tmp_path / "aliases.db")
        await store.connect()
        try:
            code = await store.create_challenge(
                initiator_id="tg:user:1", platform="telegram"
            )
            pool = _make_pool(store)
            # Complete from the *same* platform
            msg = _make_msg(user_id="tg:user:2", platform="telegram", is_admin=True)
            response = await cmd_link(msg, pool, [code])
            assert "different platform" in response.content.lower()
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_link_invalid_code(self, tmp_path: Path) -> None:
        """Completing with a wrong code returns an error."""
        store = IdentityAliasStore(db_path=tmp_path / "aliases.db")
        await store.connect()
        try:
            pool = _make_pool(store)
            msg = _make_msg(user_id="dc:user:2", platform="discord", is_admin=True)
            response = await cmd_link(msg, pool, ["BADCOD"])
            result = response.content.lower()
            assert "invalid" in result or "expired" in result
        finally:
            await store.close()


@pytest.mark.asyncio
async def test_link_complete_blocked_initiator_rejected(tmp_path: Path) -> None:
    """SC #13: /link rejected if either identity is BLOCKED."""
    from lyra.core.authenticator import Authenticator
    from lyra.core.stores.auth_store import AuthStore

    store = IdentityAliasStore(db_path=tmp_path / "alias.db")
    await store.connect()
    auth_store = AuthStore(db_path=tmp_path / "auth.db")
    await auth_store.connect()
    try:
        await auth_store.upsert(
            "tg:user:blocked", TrustLevel.BLOCKED, None, "test", "test"
        )
        auth = Authenticator(store=auth_store, role_map={}, default=TrustLevel.TRUSTED)

        # Create challenge from the blocked initiator
        code = await store.create_challenge("tg:user:blocked", "telegram")

        # Set up pool with populated authenticators
        hub_mock = MagicMock()
        hub_mock._alias_store = store
        hub_mock._authenticators = {
            ("discord", "main"): auth,
            ("telegram", "main"): auth,
        }
        pool = MagicMock(spec=Pool)
        pool._ctx = hub_mock

        # Try to complete from discord
        msg = _make_msg(user_id="dc:user:2", platform="discord", is_admin=True)
        response = await cmd_link(msg, pool, [code])
        content = response.content.lower()
        assert "blocked" in content or "cannot" in content
    finally:
        await auth_store.close()
        await store.close()


# ---------------------------------------------------------------------------
# /unlink
# ---------------------------------------------------------------------------


class TestUnlink:
    @pytest.mark.asyncio
    async def test_unlink_removes_alias(self, tmp_path: Path) -> None:
        """/unlink removes an existing alias and confirms removal."""
        store = IdentityAliasStore(db_path=tmp_path / "aliases.db")
        await store.connect()
        try:
            await store.link("tg:user:1", "dc:user:2")
            pool = _make_pool(store)
            msg = _make_msg(user_id="dc:user:2", platform="discord", is_admin=True)
            response = await cmd_unlink(msg, pool, [])
            result = response.content.lower()
            assert "removed" in result or "unlinked" in result

            aliases = store.resolve_aliases("dc:user:2")
            assert aliases == frozenset({"dc:user:2"})
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_unlink_non_admin_rejected(self, tmp_path: Path) -> None:
        """/unlink is admin-only — non-admin receives an error."""
        store = IdentityAliasStore(db_path=tmp_path / "aliases.db")
        await store.connect()
        try:
            pool = _make_pool(store)
            msg = _make_msg(is_admin=False)
            response = await cmd_unlink(msg, pool, [])
            assert "admin" in response.content.lower()
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_unlink_when_no_alias(self, tmp_path: Path) -> None:
        """/unlink when no alias exists returns appropriate message."""
        store = IdentityAliasStore(db_path=tmp_path / "aliases.db")
        await store.connect()
        try:
            pool = _make_pool(store)
            msg = _make_msg(user_id="tg:user:99", is_admin=True)
            response = await cmd_unlink(msg, pool, [])
            result = response.content.lower()
            assert "no" in result or "not found" in result
        finally:
            await store.close()
