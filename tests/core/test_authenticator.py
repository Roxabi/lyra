"""Tests for Authenticator (renamed from AuthMiddleware)."""

from __future__ import annotations

from unittest.mock import MagicMock

from factory.core.auth.authenticator import (
    _ALLOW_ALL,
    _DENY_ALL,
    Authenticator,
    AuthenticatorDeps,
)
from factory.core.auth.identity import Identity
from factory.core.auth.trust import TrustLevel


class TestResolve:
    """Authenticator.resolve() returns Identity with correct trust + admin."""

    def test_anonymous_returns_blocked_not_admin(self) -> None:
        auth = Authenticator(
            AuthenticatorDeps(store=None, role_map={}, default=TrustLevel.PUBLIC)
        )
        identity = auth.resolve(None)
        assert identity == Identity(
            user_id="", trust_level=TrustLevel.BLOCKED, is_admin=False
        )

    def test_known_user_is_trusted_not_admin_by_default(self) -> None:
        store = MagicMock()
        store.check.return_value = TrustLevel.OWNER
        auth = Authenticator(
            AuthenticatorDeps(store=store, role_map={}, default=TrustLevel.PUBLIC)
        )
        identity = auth.resolve("u1")
        assert identity.trust_level == TrustLevel.TRUSTED
        assert identity.is_admin is False

    def test_admin_user_ids_sets_is_admin_not_owner_trust(self) -> None:
        auth = Authenticator(
            AuthenticatorDeps(
                store=None,
                role_map={},
                default=TrustLevel.PUBLIC,
                admin_user_ids=frozenset({"u1"}),
            )
        )
        identity = auth.resolve("u1")
        assert identity.trust_level == TrustLevel.TRUSTED
        assert identity.is_admin is True

    def test_non_admin_user(self) -> None:
        auth = Authenticator(
            AuthenticatorDeps(
                store=None,
                role_map={},
                default=TrustLevel.PUBLIC,
                admin_user_ids=frozenset({"other"}),
            )
        )
        identity = auth.resolve("u1")
        assert identity.trust_level == TrustLevel.TRUSTED
        assert identity.is_admin is False

    def test_public_command_bypass(self) -> None:
        store = MagicMock()
        store.check.return_value = TrustLevel.PUBLIC
        auth = Authenticator(
            AuthenticatorDeps(
                store=store,
                role_map={},
                default=TrustLevel.BLOCKED,
                public_commands=["/join"],
            )
        )
        identity = auth.resolve("u1", command="/join")
        assert identity.trust_level == TrustLevel.PUBLIC

    def test_blocked_user_denied_even_public_command(self) -> None:
        store = MagicMock()
        store.check.return_value = TrustLevel.BLOCKED
        auth = Authenticator(
            AuthenticatorDeps(
                store=store,
                role_map={},
                default=TrustLevel.BLOCKED,
                public_commands=["/join"],
            )
        )
        identity = auth.resolve("u1", command="/join")
        assert identity.trust_level == TrustLevel.BLOCKED
        assert identity.is_admin is False

    def test_role_map_is_ignored_for_trust(self) -> None:
        auth = Authenticator(
            AuthenticatorDeps(
                store=None,
                role_map={"role1": TrustLevel.TRUSTED},
                default=TrustLevel.BLOCKED,
            )
        )
        identity = auth.resolve("u1", roles=["role1"])
        assert identity.trust_level == TrustLevel.TRUSTED

    def test_default_is_ignored_for_trust(self) -> None:
        auth = Authenticator(
            AuthenticatorDeps(store=None, role_map={}, default=TrustLevel.BLOCKED)
        )
        identity = auth.resolve("u1")
        assert identity.trust_level == TrustLevel.TRUSTED

    def test_admin_retains_is_admin_on_public_command(self) -> None:
        auth = Authenticator(
            AuthenticatorDeps(
                store=None,
                role_map={},
                default=TrustLevel.BLOCKED,
                public_commands=["/join"],
                admin_user_ids=frozenset({"u1"}),
            )
        )
        identity = auth.resolve("u1", command="/join")
        assert identity.trust_level == TrustLevel.PUBLIC
        assert identity.is_admin is True


class TestCheckBackwardCompat:
    """check() still returns TrustLevel for backward compat."""

    def test_check_returns_trust_level(self) -> None:
        auth = Authenticator(
            AuthenticatorDeps(store=None, role_map={}, default=TrustLevel.PUBLIC)
        )
        result = auth.check("u1")
        assert isinstance(result, TrustLevel)
        assert result == TrustLevel.TRUSTED


class TestSentinels:
    """_ALLOW_ALL and _DENY_ALL produce correct Identity."""

    def test_allow_all_resolves_trusted(self) -> None:
        identity = _ALLOW_ALL.resolve("u1")
        assert identity.trust_level == TrustLevel.TRUSTED
        assert identity.is_admin is False

    def test_deny_all_resolves_blocked(self) -> None:
        identity = _DENY_ALL.resolve("u1")
        assert identity.trust_level == TrustLevel.BLOCKED
        assert identity.is_admin is False