"""Unit tests for AuthMiddleware.check() and store-integrated behaviour.

Issue #151 S1, #245 S2. ADR-090: trust is ban-only; agent grants gate access.
"""

from __future__ import annotations

from pathlib import Path

from factory.core.auth.authenticator import (
    Authenticator as AuthMiddleware,
)
from factory.core.auth.authenticator import (
    AuthenticatorDeps,
)
from factory.core.auth.trust import TrustLevel
from factory.infrastructure.stores.identity.auth_store import AuthStore

# ---------------------------------------------------------------------------
# TestAuthMiddleware
# ---------------------------------------------------------------------------


class TestAuthMiddleware:
    def test_check_returns_trusted_for_unknown_user(self) -> None:
        auth = AuthMiddleware(
            AuthenticatorDeps(store=None, role_map={}, default=TrustLevel.BLOCKED)
        )
        assert auth.check("unknown") == TrustLevel.TRUSTED

    def test_check_none_user_returns_blocked(self) -> None:
        auth = AuthMiddleware(
            AuthenticatorDeps(store=None, role_map={}, default=TrustLevel.PUBLIC)
        )
        assert auth.check(None) == TrustLevel.BLOCKED

    async def test_stored_owner_is_trusted_not_owner(self, auth_store: AuthStore) -> None:  # noqa: E501
        await auth_store.upsert(
            "alice", TrustLevel.OWNER, None, "config", "config.toml"
        )
        auth = AuthMiddleware(
            AuthenticatorDeps(store=auth_store, role_map={}, default=TrustLevel.BLOCKED)
        )
        assert auth.check("alice") == TrustLevel.TRUSTED

    async def test_blocked_in_store_wins_over_roles(
        self, auth_store: AuthStore
    ) -> None:
        await auth_store.upsert(
            "alice", TrustLevel.BLOCKED, None, "config", "config.toml"
        )
        auth = AuthMiddleware(
            AuthenticatorDeps(
                store=auth_store,
                role_map={"admin": TrustLevel.OWNER},
                default=TrustLevel.PUBLIC,
            )
        )
        assert auth.check("alice", roles=["admin"]) == TrustLevel.BLOCKED

    def test_role_map_is_ignored(self) -> None:
        auth = AuthMiddleware(
            AuthenticatorDeps(
                store=None,
                role_map={"admin": TrustLevel.TRUSTED},
                default=TrustLevel.BLOCKED,
            )
        )
        assert auth.check("unknown_user", roles=["admin"]) == TrustLevel.TRUSTED

    def test_default_is_ignored(self) -> None:
        auth = AuthMiddleware(
            AuthenticatorDeps(
                store=None,
                role_map={"admin": TrustLevel.TRUSTED},
                default=TrustLevel.BLOCKED,
            )
        )
        assert auth.check("user", roles=["member"]) == TrustLevel.TRUSTED
        assert auth.check("user", roles=[]) == TrustLevel.TRUSTED


# ---------------------------------------------------------------------------
# TestAuthMiddlewareWithStore
# ---------------------------------------------------------------------------


class TestAuthMiddlewareWithStore:
    async def test_seeded_owner_user_returns_trusted(self, auth_store: AuthStore) -> None:  # noqa: E501
        await auth_store.upsert(
            "owner-uid", TrustLevel.OWNER, None, "config", "config.toml"
        )
        auth = AuthMiddleware(
            AuthenticatorDeps(store=auth_store, role_map={}, default=TrustLevel.PUBLIC)
        )
        assert auth.check("owner-uid") == TrustLevel.TRUSTED

    async def test_seeded_blocked_user_returns_blocked(
        self, auth_store: AuthStore
    ) -> None:
        await auth_store.upsert(
            "blocked-uid", TrustLevel.BLOCKED, None, "config", "config.toml"
        )
        auth = AuthMiddleware(
            AuthenticatorDeps(store=auth_store, role_map={}, default=TrustLevel.PUBLIC)
        )
        assert auth.check("blocked-uid") == TrustLevel.BLOCKED

    async def test_join_command_blocked_for_blocked_user(
        self, auth_store: AuthStore
    ) -> None:
        await auth_store.upsert(
            "blocked-join", TrustLevel.BLOCKED, None, "config", "config.toml"
        )
        auth = AuthMiddleware(
            AuthenticatorDeps(store=auth_store, role_map={}, default=TrustLevel.BLOCKED)
        )
        result = auth.check("blocked-join", command="/join")
        assert result == TrustLevel.BLOCKED

    async def test_join_command_returns_public_for_non_blocked_user(
        self, auth_store: AuthStore
    ) -> None:
        auth = AuthMiddleware(
            AuthenticatorDeps(store=auth_store, role_map={}, default=TrustLevel.PUBLIC)
        )
        result = auth.check("unknown-user", command="/join")
        assert result == TrustLevel.PUBLIC

    async def test_store_none_ignores_role_map_and_default(self) -> None:
        auth = AuthMiddleware(
            AuthenticatorDeps(
                store=None,
                role_map={"admin": TrustLevel.TRUSTED},
                default=TrustLevel.BLOCKED,
            )
        )
        assert auth.check("unknown") == TrustLevel.TRUSTED
        assert auth.check("unknown", roles=["admin"]) == TrustLevel.TRUSTED

    async def test_stored_public_grant_is_trusted(
        self, tmp_path: Path, auth_store: AuthStore
    ) -> None:
        await auth_store.upsert("alice", TrustLevel.PUBLIC, None, "test", "test")
        mw = AuthMiddleware(
            AuthenticatorDeps(
                store=auth_store,
                role_map={"admin_role": TrustLevel.OWNER},
                default=TrustLevel.PUBLIC,
            )
        )
        assert mw.check("alice") == TrustLevel.TRUSTED
        assert mw.check("alice", roles=["admin_role"]) == TrustLevel.TRUSTED