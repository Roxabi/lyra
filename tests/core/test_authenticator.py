"""Tests for Authenticator (renamed from AuthMiddleware)."""

from __future__ import annotations

from unittest.mock import MagicMock

from lyra.core.authenticator import _ALLOW_ALL, _DENY_ALL, Authenticator
from lyra.core.identity import Identity
from lyra.core.trust import TrustLevel


class TestResolve:
    """Authenticator.resolve() returns Identity with correct trust + admin."""

    def test_anonymous_returns_blocked_not_admin(self) -> None:
        auth = Authenticator(store=None, role_map={}, default=TrustLevel.PUBLIC)
        identity = auth.resolve(None)
        assert identity == Identity(
            user_id="", trust_level=TrustLevel.BLOCKED, is_admin=False
        )

    def test_owner_store_user_is_admin(self) -> None:
        store = MagicMock()
        store.check.return_value = TrustLevel.OWNER
        auth = Authenticator(store=store, role_map={}, default=TrustLevel.PUBLIC)
        identity = auth.resolve("u1")
        assert identity.trust_level == TrustLevel.OWNER
        assert identity.is_admin is True

    def test_admin_user_ids_sets_is_admin_and_owner_trust(self) -> None:
        # admin_user_ids grants OWNER trust regardless of store/default —
        # prevents cache-key format mismatch (tg:user: vs bare ID) blocking admins.
        auth = Authenticator(
            store=None,
            role_map={},
            default=TrustLevel.PUBLIC,
            admin_user_ids=frozenset({"u1"}),
        )
        identity = auth.resolve("u1")
        assert identity.trust_level == TrustLevel.OWNER
        assert identity.is_admin is True

    def test_non_admin_user(self) -> None:
        auth = Authenticator(
            store=None,
            role_map={},
            default=TrustLevel.PUBLIC,
            admin_user_ids=frozenset({"other"}),
        )
        identity = auth.resolve("u1")
        assert identity.trust_level == TrustLevel.PUBLIC
        assert identity.is_admin is False

    def test_public_command_bypass(self) -> None:
        store = MagicMock()
        store.check.return_value = TrustLevel.PUBLIC
        auth = Authenticator(
            store=store,
            role_map={},
            default=TrustLevel.BLOCKED,
            public_commands=["/join"],
        )
        identity = auth.resolve("u1", command="/join")
        assert identity.trust_level == TrustLevel.PUBLIC

    def test_blocked_user_denied_even_public_command(self) -> None:
        store = MagicMock()
        store.check.return_value = TrustLevel.BLOCKED
        auth = Authenticator(
            store=store,
            role_map={},
            default=TrustLevel.BLOCKED,
            public_commands=["/join"],
        )
        identity = auth.resolve("u1", command="/join")
        assert identity.trust_level == TrustLevel.BLOCKED
        assert identity.is_admin is False

    def test_role_map_resolution(self) -> None:
        auth = Authenticator(
            store=None,
            role_map={"role1": TrustLevel.TRUSTED},
            default=TrustLevel.PUBLIC,
        )
        identity = auth.resolve("u1", roles=["role1"])
        assert identity.trust_level == TrustLevel.TRUSTED

    def test_default_fallback(self) -> None:
        auth = Authenticator(store=None, role_map={}, default=TrustLevel.BLOCKED)
        identity = auth.resolve("u1")
        assert identity.trust_level == TrustLevel.BLOCKED

    def test_owner_retains_is_admin_on_public_command(self) -> None:
        """OWNER user issuing a public command retains is_admin=True."""
        store = MagicMock()
        store.check.return_value = TrustLevel.OWNER
        auth = Authenticator(
            store=store,
            role_map={},
            default=TrustLevel.BLOCKED,
            public_commands=["/join"],
        )
        identity = auth.resolve("u1", command="/join")
        # trust_level is PUBLIC (public command bypass)
        assert identity.trust_level == TrustLevel.PUBLIC
        # but is_admin remains True (stored trust is OWNER)
        assert identity.is_admin is True


class TestCheckBackwardCompat:
    """check() still returns TrustLevel for backward compat."""

    def test_check_returns_trust_level(self) -> None:
        auth = Authenticator(store=None, role_map={}, default=TrustLevel.PUBLIC)
        result = auth.check("u1")
        assert isinstance(result, TrustLevel)
        assert result == TrustLevel.PUBLIC


class TestSentinels:
    """_ALLOW_ALL and _DENY_ALL produce correct Identity."""

    def test_allow_all_resolves_public(self) -> None:
        identity = _ALLOW_ALL.resolve("u1")
        assert identity.trust_level == TrustLevel.PUBLIC
        assert identity.is_admin is False

    def test_deny_all_resolves_blocked(self) -> None:
        identity = _DENY_ALL.resolve("u1")
        assert identity.trust_level == TrustLevel.BLOCKED
        assert identity.is_admin is False
