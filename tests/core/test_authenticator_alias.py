"""Tests for Authenticator alias-awareness — cross-platform trust resolution (#472)."""

from __future__ import annotations

from pathlib import Path

import pytest

from lyra.core.authenticator import Authenticator
from lyra.core.stores.auth_store import AuthStore
from lyra.core.stores.identity_alias_store import IdentityAliasStore
from lyra.core.trust import TrustLevel

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def auth_store(tmp_path: Path):
    store = AuthStore(db_path=tmp_path / "grants.db")
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


def make_auth(
    auth_store: AuthStore,
    alias_store: IdentityAliasStore | None,
    *,
    admin_user_ids: frozenset[str] = frozenset(),
) -> Authenticator:
    return Authenticator(
        store=auth_store,
        role_map={},
        default=TrustLevel.PUBLIC,
        admin_user_ids=admin_user_ids,
        alias_store=alias_store,
    )


# ---------------------------------------------------------------------------
# Trust resolution across aliases
# ---------------------------------------------------------------------------


class TestMaxTrustAcrossAliases:
    @pytest.mark.asyncio
    async def test_max_trust_across_aliases(
        self, auth_store: AuthStore, alias_store: IdentityAliasStore
    ) -> None:
        """Max trust level across all linked IDs."""
        # tg:user:1=OWNER, dc:user:2=TRUSTED; linked → dc:user:2 resolves OWNER
        await auth_store.upsert(
            identity_key="tg:user:1",
            trust_level=TrustLevel.OWNER,
            expires_at=None,
            granted_by="test",
            source="test",
        )
        await auth_store.upsert(
            identity_key="dc:user:2",
            trust_level=TrustLevel.TRUSTED,
            expires_at=None,
            granted_by="test",
            source="test",
        )
        await alias_store.link("tg:user:1", "dc:user:2")

        auth = make_auth(auth_store, alias_store)
        identity = auth.resolve("dc:user:2")
        assert identity.trust_level == TrustLevel.OWNER

    @pytest.mark.asyncio
    async def test_any_blocked_returns_blocked(
        self, auth_store: AuthStore, alias_store: IdentityAliasStore
    ) -> None:
        """If any linked ID is BLOCKED, the entire group resolves as BLOCKED."""
        await auth_store.upsert(
            identity_key="tg:user:1",
            trust_level=TrustLevel.OWNER,
            expires_at=None,
            granted_by="test",
            source="test",
        )
        await auth_store.upsert(
            identity_key="dc:user:2",
            trust_level=TrustLevel.BLOCKED,
            expires_at=None,
            granted_by="test",
            source="test",
        )
        await alias_store.link("tg:user:1", "dc:user:2")

        auth = make_auth(auth_store, alias_store)
        # Resolving the OWNER ID also returns BLOCKED because dc:user:2 is BLOCKED
        identity = auth.resolve("tg:user:1")
        assert identity.trust_level == TrustLevel.BLOCKED

    @pytest.mark.asyncio
    async def test_blocked_prevents_escalation(
        self, auth_store: AuthStore, alias_store: IdentityAliasStore
    ) -> None:
        """BLOCKED on one alias cannot be elevated by OWNER on another."""
        await auth_store.upsert(
            identity_key="tg:user:1",
            trust_level=TrustLevel.OWNER,
            expires_at=None,
            granted_by="test",
            source="test",
        )
        await auth_store.upsert(
            identity_key="dc:user:2",
            trust_level=TrustLevel.BLOCKED,
            expires_at=None,
            granted_by="test",
            source="test",
        )
        await alias_store.link("tg:user:1", "dc:user:2")

        auth = make_auth(auth_store, alias_store)
        # Completer (dc:user:2) is blocked → must not be escalated to OWNER
        identity = auth.resolve("dc:user:2")
        assert identity.trust_level == TrustLevel.BLOCKED


# ---------------------------------------------------------------------------
# Admin flag propagation
# ---------------------------------------------------------------------------


class TestAdminCascades:
    @pytest.mark.asyncio
    async def test_is_admin_cascades(
        self, auth_store: AuthStore, alias_store: IdentityAliasStore
    ) -> None:
        """admin_user_ids containing a linked alias propagates is_admin to requester."""
        await alias_store.link("tg:user:1", "dc:user:2")

        auth = make_auth(
            auth_store, alias_store, admin_user_ids=frozenset({"tg:user:1"})
        )
        identity = auth.resolve("dc:user:2")
        assert identity.is_admin is True

    @pytest.mark.asyncio
    async def test_owner_on_alias_sets_is_admin(
        self, auth_store: AuthStore, alias_store: IdentityAliasStore
    ) -> None:
        """If any linked ID has stored OWNER trust, is_admin becomes True."""
        await auth_store.upsert(
            identity_key="tg:user:1",
            trust_level=TrustLevel.OWNER,
            expires_at=None,
            granted_by="test",
            source="test",
        )
        await alias_store.link("tg:user:1", "dc:user:2")

        auth = make_auth(auth_store, alias_store)
        identity = auth.resolve("dc:user:2")
        assert identity.is_admin is True


# ---------------------------------------------------------------------------
# Backward compatibility (no alias_store)
# ---------------------------------------------------------------------------


class TestNoAliasStoreBackwardCompat:
    @pytest.mark.asyncio
    async def test_no_alias_store_backward_compat(
        self, auth_store: AuthStore
    ) -> None:
        """Authenticator without alias_store resolves user_id directly."""
        await auth_store.upsert(
            identity_key="tg:user:1",
            trust_level=TrustLevel.OWNER,
            expires_at=None,
            granted_by="test",
            source="test",
        )
        auth = make_auth(auth_store, alias_store=None)
        identity = auth.resolve("tg:user:1")
        assert identity.trust_level == TrustLevel.OWNER
        assert identity.is_admin is True

    @pytest.mark.asyncio
    async def test_no_alias_store_unknown_user(
        self, auth_store: AuthStore
    ) -> None:
        """Without alias_store, unknown users fall back to default trust."""
        auth = make_auth(auth_store, alias_store=None)
        identity = auth.resolve("dc:user:99")
        assert identity.trust_level == TrustLevel.PUBLIC
        assert identity.is_admin is False
