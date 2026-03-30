"""Tests for the --adapter flag feature in __main__.py.

Tests cover:
  - _parse_args(): argument parsing with valid and invalid adapter values
  - _main(): adapter filtering zeroes out the inactive platform's bot list
"""

from __future__ import annotations

import asyncio

import pytest

import lyra.__main__ as main_mod
from lyra.config import DiscordMultiConfig, TelegramMultiConfig

# ---------------------------------------------------------------------------
# T1 — _parse_args: argument parsing
# ---------------------------------------------------------------------------


class TestParseArgs:
    def test_no_args_defaults_to_all(self) -> None:
        """_parse_args() with no arguments returns adapter='all'."""
        # Arrange / Act
        result = main_mod._parse_args([])

        # Assert
        assert result.adapter == "all"

    def test_adapter_telegram(self) -> None:
        """_parse_args(['--adapter', 'telegram']) returns adapter='telegram'."""
        # Arrange / Act
        result = main_mod._parse_args(["--adapter", "telegram"])

        # Assert
        assert result.adapter == "telegram"

    def test_adapter_discord(self) -> None:
        """_parse_args(['--adapter', 'discord']) returns adapter='discord'."""
        # Arrange / Act
        result = main_mod._parse_args(["--adapter", "discord"])

        # Assert
        assert result.adapter == "discord"

    def test_adapter_all_explicit(self) -> None:
        """_parse_args(['--adapter', 'all']) returns adapter='all'."""
        # Arrange / Act
        result = main_mod._parse_args(["--adapter", "all"])

        # Assert
        assert result.adapter == "all"

    def test_invalid_adapter_raises_system_exit(self) -> None:
        """_parse_args(['--adapter', 'invalid']) raises SystemExit."""
        # Arrange / Act / Assert
        with pytest.raises(SystemExit):
            main_mod._parse_args(["--adapter", "invalid"])


# ---------------------------------------------------------------------------
# T2 — _main: filtering bots before _bootstrap_multibot is called
# ---------------------------------------------------------------------------


class TestMainAdapterFiltering:
    """Test that _main() zeroes out the inactive platform's bot list."""

    async def test_telegram_only_zeroes_discord_bots(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--adapter telegram: dc_multi_cfg.bots == [] before bootstrap."""
        # Arrange
        from lyra.config import DiscordBotConfig, TelegramBotConfig

        tg_bot = TelegramBotConfig(bot_id="tg1")
        dc_bot = DiscordBotConfig(bot_id="dc1")

        captured_dc_cfg: list[DiscordMultiConfig] = []
        captured_tg_cfg: list[TelegramMultiConfig] = []

        async def fake_bootstrap_multibot(
            raw_config,
            circuit_registry,
            admin_user_ids,
            tg_multi_cfg,
            dc_multi_cfg,
            *,
            _stop=None,
        ):
            captured_tg_cfg.append(tg_multi_cfg)
            captured_dc_cfg.append(dc_multi_cfg)

        monkeypatch.setattr(main_mod, "load_dotenv", lambda: None)
        monkeypatch.setattr(main_mod, "_load_raw_config", lambda: {})
        monkeypatch.setattr(
            main_mod,
            "load_multibot_config",
            lambda raw: (
                TelegramMultiConfig(bots=[tg_bot]),
                DiscordMultiConfig(bots=[dc_bot]),
            ),
        )
        monkeypatch.setattr(main_mod, "_bootstrap_multibot", fake_bootstrap_multibot)

        stop = asyncio.Event()
        stop.set()

        # Act
        await main_mod._main(adapter="telegram", _stop=stop)

        # Assert
        assert len(captured_dc_cfg) == 1
        assert captured_dc_cfg[0].bots == [], (
            "dc_multi_cfg.bots should be [] when adapter='telegram'"
        )
        assert len(captured_tg_cfg) == 1
        assert len(captured_tg_cfg[0].bots) == 1, (
            "tg_multi_cfg.bots should be preserved when adapter='telegram'"
        )

    async def test_discord_only_zeroes_telegram_bots(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--adapter discord: tg_multi_cfg.bots == [] before bootstrap."""
        # Arrange
        from lyra.config import DiscordBotConfig, TelegramBotConfig

        tg_bot = TelegramBotConfig(bot_id="tg1")
        dc_bot = DiscordBotConfig(bot_id="dc1")

        captured_dc_cfg: list[DiscordMultiConfig] = []
        captured_tg_cfg: list[TelegramMultiConfig] = []

        async def fake_bootstrap_multibot(
            raw_config,
            circuit_registry,
            admin_user_ids,
            tg_multi_cfg,
            dc_multi_cfg,
            *,
            _stop=None,
        ):
            captured_tg_cfg.append(tg_multi_cfg)
            captured_dc_cfg.append(dc_multi_cfg)

        monkeypatch.setattr(main_mod, "load_dotenv", lambda: None)
        monkeypatch.setattr(main_mod, "_load_raw_config", lambda: {})
        monkeypatch.setattr(
            main_mod,
            "load_multibot_config",
            lambda raw: (
                TelegramMultiConfig(bots=[tg_bot]),
                DiscordMultiConfig(bots=[dc_bot]),
            ),
        )
        monkeypatch.setattr(main_mod, "_bootstrap_multibot", fake_bootstrap_multibot)

        stop = asyncio.Event()
        stop.set()

        # Act
        await main_mod._main(adapter="discord", _stop=stop)

        # Assert
        assert len(captured_tg_cfg) == 1
        assert captured_tg_cfg[0].bots == [], (
            "tg_multi_cfg.bots should be [] when adapter='discord'"
        )
        assert len(captured_dc_cfg) == 1
        assert len(captured_dc_cfg[0].bots) == 1, (
            "dc_multi_cfg.bots should be preserved when adapter='discord'"
        )

    async def test_all_preserves_both_bot_lists(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--adapter all (default): both tg and dc bot lists pass through unchanged."""
        # Arrange
        from lyra.config import DiscordBotConfig, TelegramBotConfig

        tg_bot = TelegramBotConfig(bot_id="tg1")
        dc_bot = DiscordBotConfig(bot_id="dc1")

        captured_dc_cfg: list[DiscordMultiConfig] = []
        captured_tg_cfg: list[TelegramMultiConfig] = []

        async def fake_bootstrap_multibot(
            raw_config,
            circuit_registry,
            admin_user_ids,
            tg_multi_cfg,
            dc_multi_cfg,
            *,
            _stop=None,
        ):
            captured_tg_cfg.append(tg_multi_cfg)
            captured_dc_cfg.append(dc_multi_cfg)

        monkeypatch.setattr(main_mod, "load_dotenv", lambda: None)
        monkeypatch.setattr(main_mod, "_load_raw_config", lambda: {})
        monkeypatch.setattr(
            main_mod,
            "load_multibot_config",
            lambda raw: (
                TelegramMultiConfig(bots=[tg_bot]),
                DiscordMultiConfig(bots=[dc_bot]),
            ),
        )
        monkeypatch.setattr(main_mod, "_bootstrap_multibot", fake_bootstrap_multibot)

        stop = asyncio.Event()
        stop.set()

        # Act
        await main_mod._main(adapter="all", _stop=stop)

        # Assert: both lists preserved unchanged
        assert len(captured_tg_cfg) == 1
        assert len(captured_tg_cfg[0].bots) == 1, (
            "tg_multi_cfg.bots should be preserved when adapter='all'"
        )
        assert len(captured_dc_cfg) == 1
        assert len(captured_dc_cfg[0].bots) == 1, (
            "dc_multi_cfg.bots should be preserved when adapter='all'"
        )
