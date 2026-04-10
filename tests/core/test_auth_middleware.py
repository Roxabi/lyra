"""Unit tests for AuthMiddleware.check() and store-integrated behaviour.

Issue #151 S1, #245 S2.
"""

from __future__ import annotations

from pathlib import Path

from lyra.core.authenticator import Authenticator as AuthMiddleware
from lyra.core.stores.auth_store import AuthStore
from lyra.core.trust import TrustLevel

# ---------------------------------------------------------------------------
# TestAuthMiddleware
# ---------------------------------------------------------------------------


class TestAuthMiddleware:
    def test_check_returns_default_for_unknown_user(self) -> None:
        auth = AuthMiddleware(store=None, role_map={}, default=TrustLevel.BLOCKED)
        assert auth.check("unknown") == TrustLevel.BLOCKED

    def test_check_none_user_returns_blocked(self) -> None:
        # None user_id is always BLOCKED regardless of default (security hardening)
        auth = AuthMiddleware(store=None, role_map={}, default=TrustLevel.PUBLIC)
        assert auth.check(None) == TrustLevel.BLOCKED

    def test_check_none_user_returns_blocked_default(self) -> None:
        auth = AuthMiddleware(store=None, role_map={}, default=TrustLevel.BLOCKED)
        assert auth.check(None) == TrustLevel.BLOCKED

    async def test_user_map_returns_mapped_level(self, auth_store: AuthStore) -> None:
        await auth_store.upsert(
            "alice", TrustLevel.OWNER, None, "config", "config.toml"
        )
        auth = AuthMiddleware(store=auth_store, role_map={}, default=TrustLevel.BLOCKED)
        assert auth.check("alice") == TrustLevel.OWNER

    async def test_user_map_precedence_over_role_map(
        self, auth_store: AuthStore
    ) -> None:
        """Explicit store grant wins over role-based trust."""
        await auth_store.upsert(
            "alice", TrustLevel.BLOCKED, None, "config", "config.toml"
        )
        auth = AuthMiddleware(
            store=auth_store,
            role_map={"admin": TrustLevel.OWNER},
            default=TrustLevel.PUBLIC,
        )
        # alice is BLOCKED in store even though she has admin role
        assert auth.check("alice", roles=["admin"]) == TrustLevel.BLOCKED

    def test_role_match_returns_trust(self) -> None:
        auth = AuthMiddleware(
            store=None,
            role_map={"admin": TrustLevel.TRUSTED},
            default=TrustLevel.BLOCKED,
        )
        assert auth.check("unknown_user", roles=["admin"]) == TrustLevel.TRUSTED

    def test_highest_trust_wins_for_multiple_roles(self) -> None:
        auth = AuthMiddleware(
            store=None,
            role_map={
                "member": TrustLevel.PUBLIC,
                "admin": TrustLevel.TRUSTED,
                "superadmin": TrustLevel.OWNER,
            },
            default=TrustLevel.BLOCKED,
        )
        assert auth.check("user", roles=["member", "admin"]) == TrustLevel.TRUSTED
        assert auth.check("user", roles=["member", "superadmin"]) == TrustLevel.OWNER

    def test_no_role_match_falls_back_to_default(self) -> None:
        auth = AuthMiddleware(
            store=None,
            role_map={"admin": TrustLevel.TRUSTED},
            default=TrustLevel.BLOCKED,
        )
        assert auth.check("user", roles=["member"]) == TrustLevel.BLOCKED

    def test_empty_roles_falls_back_to_default(self) -> None:
        auth = AuthMiddleware(store=None, role_map={}, default=TrustLevel.PUBLIC)
        assert auth.check("user", roles=[]) == TrustLevel.PUBLIC


# ---------------------------------------------------------------------------
# TestAuthMiddlewareWithStore (RED — new tests for #245 S2)
# ---------------------------------------------------------------------------


class TestAuthMiddlewareWithStore:
    """AuthMiddleware reads trust level from AuthStore when store is provided.

    These tests fail until S2 (AuthMiddleware refactor) is implemented.
    store=None backward-compat path must still work.
    """

    def _make_raw(self, section: str = "telegram") -> dict:
        return {
            "auth": {
                section: {
                    "owner_users": [],
                    "trusted_users": [],
                    "default": "public",
                }
            }
        }

    async def test_seeded_owner_user_returns_owner(self, auth_store: AuthStore) -> None:
        await auth_store.upsert(
            "owner-uid", TrustLevel.OWNER, None, "config", "config.toml"
        )
        auth = AuthMiddleware(store=auth_store, role_map={}, default=TrustLevel.PUBLIC)
        assert auth.check("owner-uid") == TrustLevel.OWNER

    async def test_seeded_blocked_user_returns_blocked(
        self, auth_store: AuthStore
    ) -> None:
        await auth_store.upsert(
            "blocked-uid", TrustLevel.BLOCKED, None, "config", "config.toml"
        )
        auth = AuthMiddleware(store=auth_store, role_map={}, default=TrustLevel.PUBLIC)
        assert auth.check("blocked-uid") == TrustLevel.BLOCKED

    async def test_join_command_blocked_for_blocked_user(
        self, auth_store: AuthStore
    ) -> None:
        """B2: BLOCKED users are denied even public commands like /join.

        The BLOCKED check fires before the public_commands bypass so a blocked
        user cannot re-pair by sending /join.
        """
        await auth_store.upsert(
            "blocked-join", TrustLevel.BLOCKED, None, "config", "config.toml"
        )
        auth = AuthMiddleware(store=auth_store, role_map={}, default=TrustLevel.BLOCKED)
        result = auth.check("blocked-join", command="/join")
        assert result == TrustLevel.BLOCKED

    async def test_join_command_returns_public_for_non_blocked_user(
        self, auth_store: AuthStore
    ) -> None:
        """public_commands bypass: /join returns PUBLIC for non-blocked users."""
        auth = AuthMiddleware(store=auth_store, role_map={}, default=TrustLevel.PUBLIC)
        result = auth.check("unknown-user", command="/join")
        assert result == TrustLevel.PUBLIC

    async def test_store_none_uses_role_map_and_default(self) -> None:
        """Backward compat: store=None falls back to role_map + default."""
        auth = AuthMiddleware(
            store=None,
            role_map={"admin": TrustLevel.TRUSTED},
            default=TrustLevel.BLOCKED,
        )
        assert auth.check("unknown") == TrustLevel.BLOCKED
        assert auth.check("unknown", roles=["admin"]) == TrustLevel.TRUSTED

    async def test_check_explicit_public_grant_falls_through_to_role_map(
        self, tmp_path: Path, auth_store: AuthStore
    ) -> None:
        """PUBLIC grant in the store falls through to role_map (PUBLIC is the default).

        After the B1 sentinel fix, only OWNER/TRUSTED/BLOCKED are returned directly
        from the store. PUBLIC grants pass through to role_map, so a user with an
        explicit PUBLIC grant can still be elevated by a role.
        This is the documented behavior: PUBLIC = 'not explicitly trusted or blocked'.
        """
        await auth_store.upsert("alice", TrustLevel.PUBLIC, None, "test", "test")
        role_map = {"admin_role": TrustLevel.OWNER}
        mw = AuthMiddleware(
            store=auth_store,
            role_map=role_map,
            default=TrustLevel.PUBLIC,
        )
        # With no roles, falls through to default
        assert mw.check("alice") == TrustLevel.PUBLIC
        # With a matching role, role wins (PUBLIC grant doesn't block role elevation)
        assert mw.check("alice", roles=["admin_role"]) == TrustLevel.OWNER
