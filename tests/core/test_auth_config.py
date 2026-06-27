"""Unit tests for AuthMiddleware.from_config() and from_bot_store() factories.

Issue #151, S1.  #1416 migrated from_bot_config -> from_bot_store.
ADR-090: default/role_map/seeded OWNER no longer affect trust (ban-only).
"""

from __future__ import annotations

import logging

import pytest

from factory.core.agent.bot_models import BotRow
from factory.core.auth.authenticator import (
    Authenticator as AuthMiddleware,
)
from factory.core.auth.authenticator import (
    FromBotStoreDeps,
)
from factory.core.auth.trust import TrustLevel
from factory.infrastructure.stores.identity.auth_store import AuthStore
from factory.infrastructure.stores.registry.bot_store import BotStore

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

    async def test_valid_config_returns_ban_only_trust(
        self, auth_store: AuthStore
    ) -> None:
        raw = self._make_raw("telegram")
        auth = AuthMiddleware.from_config(raw, "telegram", store=auth_store)
        assert auth is not None
        assert auth.check("tg:user:owner1") == TrustLevel.TRUSTED
        assert auth.check("tg:user:trusted1") == TrustLevel.TRUSTED
        assert auth.check("unknown") == TrustLevel.TRUSTED
        assert auth.check("unknown", roles=["admin"]) == TrustLevel.TRUSTED

    def test_missing_section_for_telegram_returns_none(self) -> None:
        assert AuthMiddleware.from_config({}, "telegram") is None

    def test_missing_section_for_discord_returns_none(self) -> None:
        assert AuthMiddleware.from_config({}, "discord") is None

    def test_missing_section_for_cli_returns_sentinel(self) -> None:
        auth = AuthMiddleware.from_config({}, "cli")
        assert auth is not None
        assert auth.check("anyone") == TrustLevel.TRUSTED
        assert auth.check(None) == TrustLevel.BLOCKED

    def test_invalid_default_raises_value_error(self) -> None:
        raw = self._make_raw("telegram", default="open")
        with pytest.raises(ValueError):
            AuthMiddleware.from_config(raw, "telegram")

    async def test_blocked_still_blocks(self, auth_store: AuthStore) -> None:
        raw = {"auth": {"telegram": {"default": "blocked"}}}
        await auth_store.upsert(
            "tg:user:42", TrustLevel.BLOCKED, None, "test", "test"
        )
        auth = AuthMiddleware.from_config(raw, "telegram", store=auth_store)
        assert auth is not None
        assert auth.check("tg:user:42") == TrustLevel.BLOCKED

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
        assert auth.check("anyone") == TrustLevel.TRUSTED

    def test_missing_section_warning_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="factory.core.auth"):
            result = AuthMiddleware.from_config({}, "telegram")
        assert result is None
        assert "telegram" in caplog.text

    def test_value_error_invalid_default_message(self) -> None:
        raw = self._make_raw("telegram", default="superadmin")
        with pytest.raises(ValueError) as exc_info:
            AuthMiddleware.from_config(raw, "telegram")
        assert "superadmin" in str(exc_info.value)


# ---------------------------------------------------------------------------
# TestFromBotStore
# ---------------------------------------------------------------------------


class TestFromBotStore:
    @staticmethod
    def _make_row(
        platform: str = "telegram",
        bot_id: str = "lyra",
        **overrides,
    ) -> BotRow:
        kwargs: dict = {
            "platform": platform,
            "bot_id": bot_id,
            "agent": "lyra_default",
        }
        kwargs.update(overrides)
        return BotRow(**kwargs)

    async def test_per_bot_match(
        self, bot_store: BotStore, auth_store: AuthStore
    ) -> None:
        row = self._make_row("telegram", "lyra")
        await bot_store.upsert(row)
        await auth_store.upsert(
            "owner1", TrustLevel.OWNER, None, "config", "config.toml"
        )
        await auth_store.upsert(
            "blocked1", TrustLevel.BLOCKED, None, "config", "config.toml"
        )
        auth = AuthMiddleware.from_bot_store(
            FromBotStoreDeps(
                platform="telegram",
                bot_id="lyra",
                bot_store=bot_store,
                store=auth_store,
            )
        )
        assert auth is not None
        assert auth.check("owner1") == TrustLevel.TRUSTED
        assert auth.check("blocked1") == TrustLevel.BLOCKED
        assert auth.check("unknown") == TrustLevel.TRUSTED
        assert auth.check("unknown", roles=["admin"]) == TrustLevel.TRUSTED

    def test_missing_bot_returns_none(self, bot_store: BotStore) -> None:
        auth = AuthMiddleware.from_bot_store(
            FromBotStoreDeps(platform="telegram", bot_id="lyra", bot_store=bot_store)
        )
        assert auth is None

    def test_neither_present_returns_none(
        self, bot_store: BotStore, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="factory.core.auth"):
            auth = AuthMiddleware.from_bot_store(
                FromBotStoreDeps(
                    platform="telegram", bot_id="lyra", bot_store=bot_store
                )
            )
        assert auth is None
        assert "lyra" in caplog.text

    def test_cli_section_returns_trusted(self, bot_store: BotStore) -> None:
        auth = AuthMiddleware.from_bot_store(
            FromBotStoreDeps(platform="cli", bot_id="main", bot_store=bot_store)
        )
        assert auth is not None
        assert auth.check("anyone") == TrustLevel.TRUSTED
        assert auth.check(None) == TrustLevel.BLOCKED