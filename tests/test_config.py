"""Unit tests for multi-bot config parsing functions in lyra.config (issue #231).

Credentials (token, webhook_secret) are no longer stored in config dataclasses
— they are resolved at bootstrap time from CredentialStore (#262).
"""

from __future__ import annotations

import logging

import pytest

from lyra.config import (
    DiscordBotConfig,
    DiscordMultiConfig,
    TelegramBotConfig,
    TelegramMultiConfig,
    _parse_discord_bots,
    _parse_telegram_bots,
    _resolve_value,
    load_multibot_config,
)

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
        with caplog.at_level(logging.WARNING, logger="lyra.config"):
            result = _resolve_value("env:MY_VAR")
        # Assert
        assert result == ""
        assert "MY_VAR" in caplog.text

    def test_env_prefix_empty_string(self, caplog: pytest.LogCaptureFixture) -> None:
        # Arrange — "env:" with no var name
        # Act
        with caplog.at_level(logging.WARNING, logger="lyra.config"):
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

    def test_happy_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Arrange — unset the username env var so the fallback "lyra_bot" is used
        monkeypatch.delenv("TELEGRAM_BOT_USERNAME", raising=False)
        raw = self._raw({"bot_id": "lyra"})
        # Act
        bots = _parse_telegram_bots(raw)
        # Assert
        assert len(bots) == 1
        bot = bots[0]
        assert isinstance(bot, TelegramBotConfig)
        assert bot.bot_id == "lyra"
        assert bot.bot_username == "lyra_bot"
        assert bot.agent == "lyra_default"

    def test_bot_username_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Arrange — set the username env var
        monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "mybot")
        raw = self._raw({"bot_id": "lyra"})
        # Act
        bots = _parse_telegram_bots(raw)
        # Assert
        assert bots[0].bot_username == "mybot"

    def test_bot_username_explicit_in_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange — explicit bot_username in entry overrides env var
        monkeypatch.delenv("TELEGRAM_BOT_USERNAME", raising=False)
        raw = self._raw({"bot_id": "lyra", "bot_username": "explicit_bot"})
        # Act
        bots = _parse_telegram_bots(raw)
        # Assert
        assert bots[0].bot_username == "explicit_bot"

    def test_two_bots_returned(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Arrange
        monkeypatch.delenv("TELEGRAM_BOT_USERNAME", raising=False)
        raw = self._raw({"bot_id": "alpha"}, {"bot_id": "beta"})
        # Act
        bots = _parse_telegram_bots(raw)
        # Assert
        assert len(bots) == 2
        assert bots[0].bot_id == "alpha"
        assert bots[1].bot_id == "beta"

    def test_custom_agent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Arrange
        monkeypatch.delenv("TELEGRAM_BOT_USERNAME", raising=False)
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

    def test_multibot_new_style(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Arrange
        monkeypatch.delenv("TELEGRAM_BOT_USERNAME", raising=False)
        raw = {"telegram": {"bots": [{"bot_id": "lyra"}]}}
        # Act
        tg, dc = load_multibot_config(raw)
        # Assert
        assert len(tg.bots) == 1
        assert tg.bots[0].bot_id == "lyra"
        assert dc.bots == []

    def test_legacy_telegram_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Arrange — no [telegram] section, but [auth.telegram] present.
        # The legacy fallback synthesizes a bot_id="main" entry regardless of env vars.
        monkeypatch.delenv("TELEGRAM_BOT_USERNAME", raising=False)
        raw = {"auth": {"telegram": {"default": "blocked"}}}
        # Act
        tg, _ = load_multibot_config(raw)
        # Assert
        assert len(tg.bots) == 1
        bot = tg.bots[0]
        assert bot.bot_id == "main"
        assert bot.bot_username == "lyra_bot"

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

    def test_new_style_wins_over_legacy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Arrange — both [[telegram.bots]] AND [auth.telegram] present
        monkeypatch.delenv("TELEGRAM_BOT_USERNAME", raising=False)
        raw = {
            "telegram": {"bots": [{"bot_id": "new_bot"}]},
            "auth": {"telegram": {"default": "blocked"}},
        }
        # Act
        tg, _ = load_multibot_config(raw)
        # Assert — only the new-style bot is present; no legacy "main" bot synthesized
        assert len(tg.bots) == 1
        assert tg.bots[0].bot_id == "new_bot"
