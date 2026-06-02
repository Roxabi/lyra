"""Unit tests for multi-bot config parsing functions in factory.config (issue #231).

Credentials (token, webhook_secret) are no longer stored in config dataclasses
— they are resolved at bootstrap time from CredentialStore (#262).
"""

from __future__ import annotations

import logging

import pytest

import factory.core.agent.bot_models as bot_models
from factory.config import (
    DISCORD_DEFAULT_AUTO_THREAD,
    DISCORD_DEFAULT_THREAD_HOT_HOURS,
    DiscordBotConfig,
    DiscordMultiConfig,
    TelegramBotConfig,
    TelegramMultiConfig,
    _parse_discord_bots,
    _parse_telegram_bots,
    _resolve_value,
    load_multibot_config,
    multibot_config_from_store,
)
from factory.core.agent.bot_models import BotRow

# ---------------------------------------------------------------------------
# TestResolveValue
# ---------------------------------------------------------------------------


class TestResolveValue:
    def test_literal_string_passthrough(self) -> None:
        # Arrange
        value = "plaintoken"
        # Act
        result = _resolve_value(value)
        # Assert
        assert result == "plaintoken"

    def test_env_prefix_resolved(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Arrange
        monkeypatch.setenv("MY_VAR", "foo")
        # Act
        result = _resolve_value("env:MY_VAR")
        # Assert
        assert result == "foo"

    def test_env_prefix_missing_var(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Arrange
        monkeypatch.delenv("MY_VAR", raising=False)
        # Act
        with caplog.at_level(logging.WARNING, logger="factory.config"):
            result = _resolve_value("env:MY_VAR")
        # Assert
        assert result == ""
        assert "MY_VAR" in caplog.text

    def test_env_prefix_empty_string(self, caplog: pytest.LogCaptureFixture) -> None:
        # Arrange — "env:" with no var name
        # Act
        with caplog.at_level(logging.WARNING, logger="factory.config"):
            result = _resolve_value("env:")
        # Assert
        assert result == ""
        assert caplog.text  # warning was logged


# ---------------------------------------------------------------------------
# TestParseTelegramBots
# ---------------------------------------------------------------------------


class TestParseTelegramBots:
    def _raw(self, *entries: dict) -> dict:
        return {"telegram": {"bots": list(entries)}}

    def test_happy_path(self) -> None:
        # Arrange
        raw = self._raw({"bot_id": "lyra"})
        # Act
        bots = _parse_telegram_bots(raw)
        # Assert
        assert len(bots) == 1
        bot = bots[0]
        assert isinstance(bot, TelegramBotConfig)
        assert bot.bot_id == "lyra"
        assert bot.agent == "lyra_default"

    def test_two_bots_returned(self) -> None:
        # Arrange
        raw = self._raw({"bot_id": "alpha"}, {"bot_id": "beta"})
        # Act
        bots = _parse_telegram_bots(raw)
        # Assert
        assert len(bots) == 2
        assert bots[0].bot_id == "alpha"
        assert bots[1].bot_id == "beta"

    def test_custom_agent(self) -> None:
        # Arrange
        raw = self._raw({"bot_id": "lyra", "agent": "my_agent"})
        # Act
        bots = _parse_telegram_bots(raw)
        # Assert
        assert bots[0].agent == "my_agent"

    def test_empty_bots_list(self) -> None:
        # Arrange — no telegram.bots key at all
        raw: dict = {}
        # Act
        bots = _parse_telegram_bots(raw)
        # Assert
        assert bots == []


# ---------------------------------------------------------------------------
# TestParseDiscordBots
# ---------------------------------------------------------------------------


class TestParseDiscordBots:
    def _raw(self, *entries: dict) -> dict:
        return {"discord": {"bots": list(entries)}}

    def test_happy_path(self) -> None:
        # Arrange
        raw = self._raw({"bot_id": "lyra"})
        # Act
        bots = _parse_discord_bots(raw)
        # Assert
        assert len(bots) == 1
        bot = bots[0]
        assert isinstance(bot, DiscordBotConfig)
        assert bot.bot_id == "lyra"
        assert bot.auto_thread is True  # default

    def test_auto_thread_false(self) -> None:
        # Arrange
        raw = self._raw({"bot_id": "lyra", "auto_thread": False})
        # Act
        bots = _parse_discord_bots(raw)
        # Assert
        assert len(bots) == 1
        assert bots[0].auto_thread is False

    def test_custom_agent(self) -> None:
        # Arrange
        raw = self._raw({"bot_id": "lyra", "agent": "my_agent"})
        # Act
        bots = _parse_discord_bots(raw)
        # Assert
        assert bots[0].agent == "my_agent"

    def test_empty_bots_list(self) -> None:
        raw: dict = {}
        bots = _parse_discord_bots(raw)
        assert bots == []


# ---------------------------------------------------------------------------
# TestLoadMultibotConfig
# ---------------------------------------------------------------------------


class TestLoadMultibotConfig:
    def test_empty_raw_returns_empty_lists(self) -> None:
        # Arrange / Act
        tg, dc = load_multibot_config({})
        # Assert
        assert isinstance(tg, TelegramMultiConfig)
        assert isinstance(dc, DiscordMultiConfig)
        assert tg.bots == []
        assert dc.bots == []

    def test_multibot_new_style(self) -> None:
        # Arrange
        raw = {"telegram": {"bots": [{"bot_id": "lyra"}]}}
        # Act
        tg, dc = load_multibot_config(raw)
        # Assert
        assert len(tg.bots) == 1
        assert tg.bots[0].bot_id == "lyra"
        assert dc.bots == []

    def test_legacy_telegram_fallback(self) -> None:
        # Arrange — no [telegram] section, but [auth.telegram] present.
        # The legacy fallback synthesizes a bot_id="main" entry regardless of env vars.
        raw = {"auth": {"telegram": {"default": "blocked"}}}
        # Act
        tg, _ = load_multibot_config(raw)
        # Assert
        assert len(tg.bots) == 1
        bot = tg.bots[0]
        assert bot.bot_id == "main"

    def test_legacy_discord_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Arrange
        monkeypatch.delenv("DISCORD_AUTO_THREAD", raising=False)
        raw = {"auth": {"discord": {"default": "blocked"}}}
        # Act
        _, dc = load_multibot_config(raw)
        # Assert
        assert len(dc.bots) == 1
        bot = dc.bots[0]
        assert bot.bot_id == "main"
        assert bot.auto_thread is True  # default when env var not set

    def test_legacy_discord_auto_thread_parsing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange — DISCORD_AUTO_THREAD=false disables threading
        monkeypatch.setenv("DISCORD_AUTO_THREAD", "false")
        raw = {"auth": {"discord": {"default": "blocked"}}}
        # Act
        _, dc = load_multibot_config(raw)
        # Assert
        assert len(dc.bots) == 1
        assert dc.bots[0].auto_thread is False

    def test_new_style_wins_over_legacy(self) -> None:
        # Arrange — both [[telegram.bots]] AND [auth.telegram] present
        raw = {
            "telegram": {"bots": [{"bot_id": "new_bot"}]},
            "auth": {"telegram": {"default": "blocked"}},
        }
        # Act
        tg, _ = load_multibot_config(raw)
        # Assert — only the new-style bot is present; no legacy "main" bot synthesized
        assert len(tg.bots) == 1
        assert tg.bots[0].bot_id == "new_bot"


# ---------------------------------------------------------------------------
# TestMultibotConfigFromStore
# ---------------------------------------------------------------------------


class _FakeBotStore:
    """Minimal in-memory BotStore stub for testing multibot_config_from_store.

    Implements the full BotStoreProtocol surface so it type-checks where the
    protocol is expected; only ``get_all`` carries behaviour for these tests.
    """

    def __init__(self, rows: list[BotRow]) -> None:
        self._rows = rows

    def get_all(self) -> list[BotRow]:
        return list(self._rows)

    def get(self, platform: str, bot_id: str) -> BotRow | None:
        return next(
            (r for r in self._rows if r.platform == platform and r.bot_id == bot_id),
            None,
        )

    async def connect(self) -> None:  # pragma: no cover - stub
        return None

    async def close(self) -> None:  # pragma: no cover - stub
        return None

    async def upsert(self, row: BotRow) -> None:  # pragma: no cover - stub
        self._rows.append(row)

    async def delete(
        self, platform: str, bot_id: str
    ) -> None:  # pragma: no cover - stub
        self._rows = [
            r for r in self._rows if not (r.platform == platform and r.bot_id == bot_id)
        ]


class TestMultibotConfigFromStore:
    def _tg_row(self, bot_id: str = "tg_bot", agent: str = "lyra_default") -> BotRow:
        return BotRow(platform="telegram", bot_id=bot_id, agent=agent)

    def _dc_row(
        self,
        bot_id: str = "dc_bot",
        agent: str = "lyra_default",
        auto_thread: bool = True,
        thread_hot_hours: int = 36,
    ) -> BotRow:
        return BotRow(
            platform="discord",
            bot_id=bot_id,
            agent=agent,
            auto_thread=auto_thread,
            thread_hot_hours=thread_hot_hours,
        )

    def test_from_store_one_telegram_one_discord(self) -> None:
        # Arrange
        store = _FakeBotStore([self._tg_row("tg_bot"), self._dc_row("dc_bot")])
        # Act
        tg, dc = multibot_config_from_store(store)
        # Assert
        assert isinstance(tg, TelegramMultiConfig)
        assert isinstance(dc, DiscordMultiConfig)
        assert len(tg.bots) == 1
        assert len(dc.bots) == 1
        assert tg.bots[0].bot_id == "tg_bot"
        assert dc.bots[0].bot_id == "dc_bot"

    def test_from_store_telegram_bot_fields_round_trip(self) -> None:
        # Arrange
        store = _FakeBotStore([self._tg_row(bot_id="lyra", agent="my_agent")])
        # Act
        tg, _ = multibot_config_from_store(store)
        # Assert
        bot = tg.bots[0]
        assert isinstance(bot, TelegramBotConfig)
        assert bot.bot_id == "lyra"
        assert bot.agent == "my_agent"

    def test_from_store_discord_bot_fields_round_trip(self) -> None:
        # Arrange
        store = _FakeBotStore(
            [self._dc_row(bot_id="dc1", auto_thread=False, thread_hot_hours=48)]
        )
        # Act
        _, dc = multibot_config_from_store(store)
        # Assert
        bot = dc.bots[0]
        assert isinstance(bot, DiscordBotConfig)
        assert bot.bot_id == "dc1"
        assert bot.auto_thread is False
        assert bot.thread_hot_hours == 48

    def test_from_store_empty_store_returns_empty_lists(self) -> None:
        # Arrange
        store = _FakeBotStore([])
        # Act
        tg, dc = multibot_config_from_store(store)
        # Assert
        assert tg.bots == []
        assert dc.bots == []

    def test_from_store_unknown_platform_ignored(self) -> None:
        # Arrange — a row with an unrecognised platform is silently skipped
        unknown = BotRow(platform="slack", bot_id="slack_bot", agent="lyra_default")
        store = _FakeBotStore([self._tg_row(), unknown])
        # Act
        tg, dc = multibot_config_from_store(store)
        # Assert
        assert len(tg.bots) == 1
        assert dc.bots == []


# ---------------------------------------------------------------------------
# TestDiscordDefaultConstants — guard test for #1514
# ---------------------------------------------------------------------------


class TestDiscordDefaultConstants:
    """Guard that the Discord-specific defaults are named, valued, and deliberately
    diverge from the generic platform-agnostic defaults in bot_models.

    If a future "alignment" refactor changes these values, this test will trip and
    force re-reading the decision documented in #1514 before proceeding.
    """

    def test_discord_default_auto_thread_is_true(self) -> None:
        # Discord threads every conversation by default.
        assert DISCORD_DEFAULT_AUTO_THREAD is True

    def test_discord_default_thread_hot_hours_is_36(self) -> None:
        assert DISCORD_DEFAULT_THREAD_HOT_HOURS == 36

    def test_discord_bot_config_default_auto_thread(self) -> None:
        # DiscordBotConfig() with no explicit auto_thread must use the named constant.
        cfg = DiscordBotConfig(bot_id="test")
        assert cfg.auto_thread is DISCORD_DEFAULT_AUTO_THREAD
        assert cfg.auto_thread is True

    def test_discord_bot_config_default_thread_hot_hours(self) -> None:
        # DiscordBotConfig() with no explicit thread_hot_hours must use the constant.
        cfg = DiscordBotConfig(bot_id="test")
        assert cfg.thread_hot_hours == DISCORD_DEFAULT_THREAD_HOT_HOURS
        assert cfg.thread_hot_hours == 36

    def test_intentional_divergence_from_generic_auto_thread(self) -> None:
        # DELIBERATE: Discord default (True) != generic default (False).
        # bot_models.DEFAULT_AUTO_THREAD is False for non-threading platforms
        # (e.g. Telegram). Discord is the exception.
        # See #1514. Do NOT "fix" this divergence without re-reading that decision.
        assert DISCORD_DEFAULT_AUTO_THREAD != bot_models.DEFAULT_AUTO_THREAD

    def test_intentional_divergence_from_generic_thread_hot_hours(self) -> None:
        # DELIBERATE: Discord default (36 h) != generic default (24 h). See #1514.
        assert DISCORD_DEFAULT_THREAD_HOT_HOURS != bot_models.DEFAULT_THREAD_HOT_HOURS
