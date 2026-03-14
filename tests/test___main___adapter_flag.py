"""RED phase tests for the --adapter flag feature in __main__.py.

Tests cover:
  - _parse_args(): argument parsing with valid and invalid adapter values
  - _main(): adapter filtering zeroes out the inactive platform's bot list
  - _bootstrap_legacy(): adapter="telegram" skips Discord AuthMiddleware.from_config

These tests FAIL intentionally until the implementation is added.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

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
        result = main_mod._parse_args([])  # type: ignore[attr-defined]

        # Assert
        assert result.adapter == "all"

    def test_adapter_telegram(self) -> None:
        """_parse_args(['--adapter', 'telegram']) returns adapter='telegram'."""
        # Arrange / Act
        result = main_mod._parse_args(["--adapter", "telegram"])  # type: ignore[attr-defined]

        # Assert
        assert result.adapter == "telegram"

    def test_adapter_discord(self) -> None:
        """_parse_args(['--adapter', 'discord']) returns adapter='discord'."""
        # Arrange / Act
        result = main_mod._parse_args(["--adapter", "discord"])  # type: ignore[attr-defined]

        # Assert
        assert result.adapter == "discord"

    def test_adapter_all_explicit(self) -> None:
        """_parse_args(['--adapter', 'all']) returns adapter='all'."""
        # Arrange / Act
        result = main_mod._parse_args(["--adapter", "all"])  # type: ignore[attr-defined]

        # Assert
        assert result.adapter == "all"

    def test_invalid_adapter_raises_system_exit(self) -> None:
        """_parse_args(['--adapter', 'invalid']) raises SystemExit."""
        # Arrange / Act / Assert
        with pytest.raises(SystemExit):
            main_mod._parse_args(["--adapter", "invalid"])  # type: ignore[attr-defined]


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

        tg_bot = TelegramBotConfig(
            bot_id="tg1",
            token="fake-tg-token",
            bot_username="lyra_bot",
            webhook_secret="fake-secret",
        )
        dc_bot = DiscordBotConfig(bot_id="dc1", token="fake-dc-token")

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
        await main_mod._main(adapter="telegram", _stop=stop)  # type: ignore[call-arg]

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

        tg_bot = TelegramBotConfig(
            bot_id="tg1",
            token="fake-tg-token",
            bot_username="lyra_bot",
            webhook_secret="fake-secret",
        )
        dc_bot = DiscordBotConfig(bot_id="dc1", token="fake-dc-token")

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
        await main_mod._main(adapter="discord", _stop=stop)  # type: ignore[call-arg]

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

        tg_bot = TelegramBotConfig(
            bot_id="tg1",
            token="fake-tg-token",
            bot_username="lyra_bot",
            webhook_secret="fake-secret",
        )
        dc_bot = DiscordBotConfig(bot_id="dc1", token="fake-dc-token")

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
        await main_mod._main(adapter="all", _stop=stop)  # type: ignore[call-arg]

        # Assert: both lists preserved unchanged
        assert len(captured_tg_cfg) == 1
        assert len(captured_tg_cfg[0].bots) == 1, (
            "tg_multi_cfg.bots should be preserved when adapter='all'"
        )
        assert len(captured_dc_cfg) == 1
        assert len(captured_dc_cfg[0].bots) == 1, (
            "dc_multi_cfg.bots should be preserved when adapter='all'"
        )


# ---------------------------------------------------------------------------
# T3 — _bootstrap_legacy: adapter="telegram" skips Discord from_config
# ---------------------------------------------------------------------------


class TestBootstrapLegacyAdapterParam:
    """Test that _bootstrap_legacy respects the adapter parameter.

    Heavy mocking is expected here: _bootstrap_legacy is a PLR0915 startup wiring
    function that initialises Hub, OutboundDispatcher, CliPool, uvicorn.Server, and
    several helper services. The mock depth is intentional — the assertion targets only
    the AuthMiddleware.from_config call-pattern at the top of the function, before any
    of the downstream wiring runs.
    """

    async def test_telegram_adapter_skips_discord_auth(  # noqa: PLR0915
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """adapter='telegram': from_config NOT called for discord."""
        # Arrange
        from_config_calls: list[str] = []

        def fake_from_config(raw, section, store=None):
            from_config_calls.append(section)
            if section == "telegram":
                return MagicMock()
            return None

        fake_auth_store = MagicMock()
        fake_auth_store.connect = AsyncMock()
        fake_auth_store.seed_from_config = AsyncMock()
        fake_auth_store.close = AsyncMock()

        monkeypatch.setattr(main_mod, "AuthStore", lambda **kwargs: fake_auth_store)
        monkeypatch.setattr(
            main_mod.AuthMiddleware,
            "from_config",
            classmethod(
                lambda cls, raw, section, store=None: fake_from_config(
                    raw, section, store
                )
            ),
        )
        monkeypatch.setattr(
            main_mod,
            "load_telegram_config",
            lambda: MagicMock(token="t", webhook_secret="s", bot_username="b"),
        )
        monkeypatch.setattr(
            main_mod,
            "load_discord_config",
            lambda: MagicMock(token="d"),
        )
        monkeypatch.setattr(
            main_mod,
            "load_agent_config",
            lambda name, **kw: MagicMock(
                name=name,
                i18n_language="en",
                model_config=MagicMock(backend="claude-cli"),
                smart_routing=None,
            ),
        )

        fake_tg_adapter = MagicMock()
        fake_tg_adapter.dp = MagicMock()
        fake_tg_adapter.dp.start_polling = AsyncMock(
            side_effect=asyncio.CancelledError
        )
        fake_tg_adapter.bot = MagicMock()
        fake_tg_adapter._bot_id = "main"

        monkeypatch.setattr(
            main_mod,
            "TelegramAdapter",
            lambda **kwargs: fake_tg_adapter,
        )

        fake_hub = MagicMock()
        fake_hub.inbound_bus = MagicMock()
        fake_hub.inbound_bus.start = AsyncMock()
        fake_hub.inbound_bus.stop = AsyncMock()
        fake_hub.inbound_audio_bus = MagicMock()
        fake_hub.inbound_audio_bus.start = AsyncMock()
        fake_hub.inbound_audio_bus.stop = AsyncMock()
        fake_hub.run = AsyncMock(side_effect=asyncio.CancelledError)
        fake_hub._audio_loop = AsyncMock(side_effect=asyncio.CancelledError)
        fake_hub.circuit_registry = None
        fake_hub._start_time = 0
        fake_hub._last_processed_at = None
        monkeypatch.setattr(main_mod, "Hub", lambda **kwargs: fake_hub)

        fake_dispatcher = MagicMock()
        fake_dispatcher.start = AsyncMock()
        fake_dispatcher.stop = AsyncMock()
        monkeypatch.setattr(
            main_mod, "OutboundDispatcher", lambda **kwargs: fake_dispatcher
        )

        fake_uvicorn_server = MagicMock()
        fake_uvicorn_server.serve = AsyncMock(side_effect=asyncio.CancelledError)
        monkeypatch.setattr(
            main_mod.uvicorn, "Server", lambda config: fake_uvicorn_server
        )

        fake_cli_pool = MagicMock()
        fake_cli_pool.start = AsyncMock()
        fake_cli_pool.stop = AsyncMock()
        monkeypatch.setattr(main_mod, "CliPool", lambda: fake_cli_pool)

        monkeypatch.setattr(
            main_mod,
            "_build_provider_registry",
            lambda *a, **kw: (MagicMock(), None),
        )
        monkeypatch.setattr(
            main_mod,
            "_create_agent",
            lambda *a, **kw: MagicMock(name="lyra_default"),
        )
        monkeypatch.setattr(
            main_mod, "_load_messages", lambda **kw: MagicMock()
        )
        monkeypatch.setattr(
            main_mod,
            "_load_pairing_config",
            lambda raw: MagicMock(enabled=False),
        )

        stop = asyncio.Event()
        stop.set()

        # Act
        # type: ignore[call-arg] — adapter param doesn't exist yet (RED phase)
        await main_mod._bootstrap_legacy(  # type: ignore[call-arg]
            {}, MagicMock(), set(), adapter="telegram", _stop=stop  # type: ignore[call-arg]
        )

        # Assert: from_config was called for telegram but NOT for discord
        assert "telegram" in from_config_calls, (
            "AuthMiddleware.from_config should be called for 'telegram'"
        )
        assert "discord" not in from_config_calls, (
            "from_config should NOT be called for 'discord' when adapter='telegram'"
        )
