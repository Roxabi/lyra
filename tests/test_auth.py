"""Unit tests for AuthMiddleware + TrustLevel (issue #151).

RED phase — these tests are expected to FAIL until the GREEN phase creates:
  - src/lyra/core/auth.py  (TrustLevel, AuthMiddleware)

Spec trace: SC-1, SC-2, SC-3, SC-4, SC-5, SC-6, SC-7, SC-16
"""

from __future__ import annotations

import pytest

from lyra.core.auth import AuthMiddleware, TrustLevel


class TestTrustLevel:
    def test_has_exactly_four_values(self) -> None:
        """SC-1: TrustLevel enum has exactly OWNER, TRUSTED, PUBLIC, BLOCKED."""
        # Arrange + Act
        values = set(TrustLevel)
        # Assert
        assert values == {
            TrustLevel.OWNER,
            TrustLevel.TRUSTED,
            TrustLevel.PUBLIC,
            TrustLevel.BLOCKED,
        }

    def test_enum_values_are_strings(self) -> None:
        """Each TrustLevel member has a string value."""
        for level in TrustLevel:
            assert isinstance(level.value, str)

    def test_owner_value(self) -> None:
        assert TrustLevel.OWNER.value == "owner"

    def test_trusted_value(self) -> None:
        assert TrustLevel.TRUSTED.value == "trusted"

    def test_public_value(self) -> None:
        assert TrustLevel.PUBLIC.value == "public"

    def test_blocked_value(self) -> None:
        assert TrustLevel.BLOCKED.value == "blocked"


class TestAuthMiddlewareCheck:
    def test_known_owner_returns_owner(self) -> None:
        """A user explicitly mapped to OWNER gets OWNER."""
        # Arrange
        auth = AuthMiddleware({"user:1": TrustLevel.OWNER}, TrustLevel.BLOCKED)
        # Act + Assert
        assert auth.check("user:1") == TrustLevel.OWNER

    def test_known_trusted_returns_trusted(self) -> None:
        """A user mapped to TRUSTED gets TRUSTED."""
        auth = AuthMiddleware({"user:2": TrustLevel.TRUSTED}, TrustLevel.BLOCKED)
        assert auth.check("user:2") == TrustLevel.TRUSTED

    def test_unknown_user_returns_default_blocked(self) -> None:
        """SC-2: unknown user_id with default=BLOCKED returns BLOCKED."""
        # Arrange
        auth = AuthMiddleware({}, TrustLevel.BLOCKED)
        # Act
        result = auth.check("user:99")
        # Assert
        assert result == TrustLevel.BLOCKED

    def test_unknown_user_returns_default_public(self) -> None:
        """Unknown user_id with default=PUBLIC returns PUBLIC."""
        auth = AuthMiddleware({}, TrustLevel.PUBLIC)
        assert auth.check("user:unknown") == TrustLevel.PUBLIC

    def test_none_user_id_returns_default(self) -> None:
        """SC-3: check(None) returns the configured default, does not raise."""
        # Arrange
        auth = AuthMiddleware({}, TrustLevel.BLOCKED)
        # Act — must not raise
        result = auth.check(None)
        # Assert
        assert result == TrustLevel.BLOCKED

    def test_none_user_id_with_public_default(self) -> None:
        """check(None) respects whatever default is configured."""
        auth = AuthMiddleware({}, TrustLevel.PUBLIC)
        assert auth.check(None) == TrustLevel.PUBLIC

    def test_blocked_check_returns_trust_level_blocked(self) -> None:
        """SC-7 (partial): BLOCKED check returns TrustLevel.BLOCKED."""
        auth = AuthMiddleware({}, TrustLevel.BLOCKED)
        result = auth.check("tg:user:999")
        assert result is TrustLevel.BLOCKED

    def test_empty_trust_map_always_returns_default(self) -> None:
        """Empty trust_map → every call falls through to default."""
        auth = AuthMiddleware({}, TrustLevel.PUBLIC)
        for uid in ["a", "b", "c", None]:
            assert auth.check(uid) == TrustLevel.PUBLIC


class TestAuthMiddlewareFromConfig:
    def test_valid_telegram_config_owner_user(self) -> None:
        """from_config parses owner_users correctly."""
        # Arrange
        raw = {
            "auth": {
                "telegram": {
                    "owner_users": ["u1"],
                    "trusted_users": [],
                    "default": "blocked",
                }
            }
        }
        # Act
        auth = AuthMiddleware.from_config(raw, "telegram")
        # Assert
        assert auth.check("u1") == TrustLevel.OWNER

    def test_valid_telegram_config_trusted_user(self) -> None:
        """from_config parses trusted_users correctly."""
        raw = {
            "auth": {
                "telegram": {
                    "owner_users": [],
                    "trusted_users": ["u2"],
                    "default": "blocked",
                }
            }
        }
        auth = AuthMiddleware.from_config(raw, "telegram")
        assert auth.check("u2") == TrustLevel.TRUSTED

    def test_valid_telegram_config_unknown_falls_to_default(self) -> None:
        """Unknown user falls through to configured default."""
        raw = {
            "auth": {
                "telegram": {
                    "owner_users": ["u1"],
                    "trusted_users": ["u2"],
                    "default": "blocked",
                }
            }
        }
        auth = AuthMiddleware.from_config(raw, "telegram")
        assert auth.check("unknown") == TrustLevel.BLOCKED

    def test_missing_telegram_section_raises_system_exit(self) -> None:
        """SC-4: from_config({}, 'telegram') raises SystemExit — fail closed."""
        with pytest.raises(SystemExit):
            AuthMiddleware.from_config({}, "telegram")

    def test_missing_telegram_in_auth_block_raises_system_exit(self) -> None:
        """[auth] present but [auth.telegram] missing → SystemExit."""
        raw = {"auth": {"discord": {"default": "blocked"}}}
        with pytest.raises(SystemExit):
            AuthMiddleware.from_config(raw, "telegram")

    def test_missing_cli_section_returns_owner_middleware(self) -> None:
        """SC-5: from_config({}, 'cli') returns fixed-OWNER middleware, no raise."""
        # Act — must not raise
        auth = AuthMiddleware.from_config({}, "cli")
        # Assert — every user gets OWNER
        assert auth.check("any:user") == TrustLevel.OWNER
        assert auth.check(None) == TrustLevel.OWNER

    def test_invalid_default_value_raises_system_exit(self) -> None:
        """SC-6: 'open' is not a valid TrustLevel → SystemExit."""
        raw = {"auth": {"telegram": {"default": "open"}}}
        with pytest.raises(SystemExit):
            AuthMiddleware.from_config(raw, "telegram")

    def test_invalid_default_random_string_raises_system_exit(self) -> None:
        """Any non-TrustLevel string in default → SystemExit."""
        raw = {"auth": {"telegram": {"default": "admin"}}}
        with pytest.raises(SystemExit):
            AuthMiddleware.from_config(raw, "telegram")

    def test_missing_lyra_toml_equivalent_raises_system_exit(self) -> None:
        """SC-16: empty raw dict (lyra.toml absent) → fail closed for telegram."""
        # An empty dict is exactly what _load_raw_config() returns when the file
        # does not exist, so this covers the 'missing lyra.toml' scenario.
        with pytest.raises(SystemExit):
            AuthMiddleware.from_config({}, "telegram")

    def test_missing_lyra_toml_equivalent_discord_raises_system_exit(self) -> None:
        """SC-16: empty raw dict → fail closed for discord too."""
        with pytest.raises(SystemExit):
            AuthMiddleware.from_config({}, "discord")

    def test_owner_beats_trusted_when_both_lists_contain_user(self) -> None:
        """If a user appears in both owner_users and trusted_users, OWNER wins."""
        raw = {
            "auth": {
                "telegram": {
                    "owner_users": ["u1"],
                    "trusted_users": ["u1"],
                    "default": "blocked",
                }
            }
        }
        auth = AuthMiddleware.from_config(raw, "telegram")
        assert auth.check("u1") == TrustLevel.OWNER

    def test_default_public_is_valid(self) -> None:
        """default='public' is a valid TrustLevel and should not raise."""
        raw = {"auth": {"telegram": {"default": "public"}}}
        auth = AuthMiddleware.from_config(raw, "telegram")
        assert auth.check("unknown") == TrustLevel.PUBLIC
