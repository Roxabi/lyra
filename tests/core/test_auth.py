"""Unit tests for TrustLevel enum and AuthMiddleware (issue #151, S1)."""

from __future__ import annotations

import logging

import pytest

from lyra.core.auth import AuthMiddleware, TrustLevel

# ---------------------------------------------------------------------------
# TestTrustLevel
# ---------------------------------------------------------------------------


class TestTrustLevel:
    def test_four_members(self) -> None:
        assert len(TrustLevel) == 4

    def test_str_values(self) -> None:
        assert TrustLevel.OWNER.value == "owner"
        assert TrustLevel.TRUSTED.value == "trusted"
        assert TrustLevel.PUBLIC.value == "public"
        assert TrustLevel.BLOCKED.value == "blocked"

    def test_is_str_subclass(self) -> None:
        assert isinstance(TrustLevel.OWNER, str)
        assert TrustLevel.OWNER == "owner"

    def test_all_members_present(self) -> None:
        names = {m.name for m in TrustLevel}
        assert names == {"OWNER", "TRUSTED", "PUBLIC", "BLOCKED"}


# ---------------------------------------------------------------------------
# TestAuthMiddleware
# ---------------------------------------------------------------------------


class TestAuthMiddleware:
    def test_check_returns_default_for_unknown_user(self) -> None:
        auth = AuthMiddleware({}, {}, default=TrustLevel.BLOCKED)
        assert auth.check("unknown") == TrustLevel.BLOCKED

    def test_check_none_user_returns_default(self) -> None:
        auth = AuthMiddleware({}, {}, default=TrustLevel.PUBLIC)
        assert auth.check(None) == TrustLevel.PUBLIC

    def test_check_none_user_returns_blocked_default(self) -> None:
        auth = AuthMiddleware({}, {}, default=TrustLevel.BLOCKED)
        assert auth.check(None) == TrustLevel.BLOCKED

    def test_user_map_returns_mapped_level(self) -> None:
        auth = AuthMiddleware(
            user_map={"alice": TrustLevel.OWNER},
            role_map={},
            default=TrustLevel.BLOCKED,
        )
        assert auth.check("alice") == TrustLevel.OWNER

    def test_user_map_precedence_over_role_map(self) -> None:
        """Explicit user assignment wins over role-based trust."""
        auth = AuthMiddleware(
            user_map={"alice": TrustLevel.BLOCKED},
            role_map={"admin": TrustLevel.OWNER},
            default=TrustLevel.PUBLIC,
        )
        # alice is in user_map (BLOCKED) even though she has admin role
        assert auth.check("alice", roles=["admin"]) == TrustLevel.BLOCKED

    def test_role_match_returns_trust(self) -> None:
        auth = AuthMiddleware(
            user_map={},
            role_map={"admin": TrustLevel.TRUSTED},
            default=TrustLevel.BLOCKED,
        )
        assert auth.check("unknown_user", roles=["admin"]) == TrustLevel.TRUSTED

    def test_highest_trust_wins_for_multiple_roles(self) -> None:
        auth = AuthMiddleware(
            user_map={},
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
            user_map={},
            role_map={"admin": TrustLevel.TRUSTED},
            default=TrustLevel.BLOCKED,
        )
        assert auth.check("user", roles=["member"]) == TrustLevel.BLOCKED

    def test_empty_roles_falls_back_to_default(self) -> None:
        auth = AuthMiddleware({}, {}, default=TrustLevel.PUBLIC)
        assert auth.check("user", roles=[]) == TrustLevel.PUBLIC


# ---------------------------------------------------------------------------
# TestFromConfig
# ---------------------------------------------------------------------------


class TestFromConfig:
    def _make_raw(self, section: str, **overrides) -> dict:
        base: dict = {
            "owner_users": ["owner1"],
            "trusted_users": ["trusted1"],
            "trusted_roles": ["admin"],
            "default": "blocked",
        }
        base.update(overrides)
        return {"auth": {section: base}}

    def test_valid_config_parses_correctly(self) -> None:
        raw = self._make_raw("telegram")
        auth = AuthMiddleware.from_config(raw, "telegram")
        assert auth is not None
        assert auth.check("owner1") == TrustLevel.OWNER
        assert auth.check("trusted1") == TrustLevel.TRUSTED
        assert auth.check("unknown") == TrustLevel.BLOCKED
        assert auth.check("unknown", roles=["admin"]) == TrustLevel.TRUSTED

    def test_missing_section_for_telegram_returns_none(self) -> None:
        assert AuthMiddleware.from_config({}, "telegram") is None

    def test_missing_section_for_discord_returns_none(self) -> None:
        assert AuthMiddleware.from_config({}, "discord") is None

    def test_missing_section_for_cli_returns_owner_middleware(self) -> None:
        auth = AuthMiddleware.from_config({}, "cli")
        assert auth is not None
        # CLI is always OWNER
        assert auth.check("anyone") == TrustLevel.OWNER
        assert auth.check(None) == TrustLevel.OWNER

    def test_invalid_default_raises_value_error(self) -> None:
        raw = self._make_raw("telegram", default="open")
        with pytest.raises(ValueError):
            AuthMiddleware.from_config(raw, "telegram")

    def test_owner_users_get_owner_level(self) -> None:
        raw = {
            "auth": {"telegram": {"owner_users": ["7377831990"], "default": "blocked"}}
        }
        auth = AuthMiddleware.from_config(raw, "telegram")
        assert auth is not None
        assert auth.check("7377831990") == TrustLevel.OWNER

    def test_trusted_users_get_trusted_level(self) -> None:
        raw = {"auth": {"telegram": {"trusted_users": ["9999"], "default": "blocked"}}}
        auth = AuthMiddleware.from_config(raw, "telegram")
        assert auth is not None
        assert auth.check("9999") == TrustLevel.TRUSTED

    def test_trusted_roles_get_trusted_level(self) -> None:
        raw = {"auth": {"discord": {"trusted_roles": ["staff"], "default": "public"}}}
        auth = AuthMiddleware.from_config(raw, "discord")
        assert auth is not None
        assert auth.check("user", roles=["staff"]) == TrustLevel.TRUSTED

    def test_owner_users_not_downgraded_by_trusted_users(self) -> None:
        """A user in both owner_users and trusted_users stays OWNER."""
        raw = {
            "auth": {
                "telegram": {
                    "owner_users": ["42"],
                    "trusted_users": ["42"],
                    "default": "blocked",
                }
            }
        }
        auth = AuthMiddleware.from_config(raw, "telegram")
        assert auth is not None
        assert auth.check("42") == TrustLevel.OWNER

    def test_empty_lists_allowed(self) -> None:
        raw = {
            "auth": {
                "telegram": {
                    "owner_users": [],
                    "trusted_users": [],
                    "trusted_roles": [],
                    "default": "public",
                }
            }
        }
        auth = AuthMiddleware.from_config(raw, "telegram")
        assert auth is not None
        assert auth.check("anyone") == TrustLevel.PUBLIC

    def test_missing_section_warning_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="lyra.core.auth"):
            result = AuthMiddleware.from_config({}, "telegram")
        assert result is None
        assert "telegram" in caplog.text

    def test_value_error_invalid_default_message(self) -> None:
        raw = self._make_raw("telegram", default="superadmin")
        with pytest.raises(ValueError) as exc_info:
            AuthMiddleware.from_config(raw, "telegram")
        assert "superadmin" in str(exc_info.value)


# ---------------------------------------------------------------------------
# TestFromBotConfig
# ---------------------------------------------------------------------------


class TestFromBotConfig:
    def _raw_with_bot(self, section: str, bot_id: str, **overrides) -> dict:
        """Build a raw config with a single per-bot auth entry."""
        entry: dict = {
            "bot_id": bot_id,
            "owner_users": ["owner1"],
            "trusted_users": ["trusted1"],
            "trusted_roles": ["admin"],
            "default": "blocked",
        }
        entry.update(overrides)
        return {"auth": {f"{section}_bots": [entry]}}

    def test_per_bot_match(self) -> None:
        # Arrange
        raw = self._raw_with_bot("telegram", "lyra")
        # Act
        auth = AuthMiddleware.from_bot_config(raw, "telegram", "lyra")
        # Assert
        assert auth is not None
        assert auth.check("owner1") == TrustLevel.OWNER
        assert auth.check("trusted1") == TrustLevel.TRUSTED
        assert auth.check("unknown") == TrustLevel.BLOCKED
        assert auth.check("unknown", roles=["admin"]) == TrustLevel.TRUSTED

    def test_no_fallback_to_flat_section(self) -> None:
        # Arrange — bot_id NOT in per-bot list, but [auth.telegram] IS present
        raw = {
            "auth": {
                "telegram": {"default": "public", "owner_users": ["owner1"]},
                "telegram_bots": [{"bot_id": "other_bot", "default": "blocked"}],
            }
        }
        # Act — looking for "lyra", which is not in telegram_bots
        auth = AuthMiddleware.from_bot_config(raw, "telegram", "lyra")
        # Assert — returns None; no fallback to flat section (security fix)
        assert auth is None

    def test_neither_present_returns_none(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Arrange — no per-bot list, no flat section
        raw: dict = {}
        # Act
        with caplog.at_level(logging.WARNING, logger="lyra.core.auth"):
            auth = AuthMiddleware.from_bot_config(raw, "telegram", "lyra")
        # Assert
        assert auth is None
        assert "lyra" in caplog.text

    def test_cli_section_returns_owner(self) -> None:
        # Arrange — section="cli", no config needed
        raw: dict = {}
        # Act
        auth = AuthMiddleware.from_bot_config(raw, "cli", "main")
        # Assert
        assert auth is not None
        assert auth.check("anyone") == TrustLevel.OWNER
        assert auth.check(None) == TrustLevel.OWNER

    def test_invalid_default_raises_with_bot_id(self) -> None:
        # Arrange — matching entry with an invalid default value
        raw = self._raw_with_bot("telegram", "lyra", default="superadmin")
        # Act / Assert
        with pytest.raises(ValueError) as exc_info:
            AuthMiddleware.from_bot_config(raw, "telegram", "lyra")
        error_msg = str(exc_info.value)
        assert "lyra" in error_msg
        assert "superadmin" in error_msg
