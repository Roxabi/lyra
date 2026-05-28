"""tool_display_config threading from raw_config to adapters — post-#1468.

Tests assert that the [tool_display] section in raw_config is loaded and threaded
through both the wired bootstrap path (bootstrap_wiring.py) and the standalone
adapter path (adapter_standalone.py) via configure_tool_display() (post-construction
setter), NOT via a constructor kwarg.

SC-5: absent [tool_display] section → ToolDisplayConfig() defaults (integration level).
SC-6: all 4 bootstrap callsites call adapter.configure_tool_display(config) after
      construction (kwarg was removed from both TelegramAdapter and DiscordAdapter
      __init__ in #1468).
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lyra.bootstrap.factory.config import _load_tool_display_config

# ---------------------------------------------------------------------------
# Helper — minimal raw_config dicts
# ---------------------------------------------------------------------------


def _tg_raw_config(tool_display: dict | None = None) -> dict:
    """Minimal raw_config with one Telegram bot and optional [tool_display] section."""
    cfg: dict = {"telegram": {"bots": [{"bot_id": "main"}]}}
    if tool_display is not None:
        cfg["tool_display"] = tool_display
    return cfg


def _dc_raw_config(tool_display: dict | None = None) -> dict:
    """Minimal raw_config with one Discord bot and optional [tool_display] section."""
    cfg: dict = {
        "discord": {
            "bots": [{"bot_id": "main", "auto_thread": False, "thread_hot_hours": 4}]
        }
    }
    if tool_display is not None:
        cfg["tool_display"] = tool_display
    return cfg


# ---------------------------------------------------------------------------
# Test 1: _load_tool_display_config unit — wired Telegram (loader helper)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wired_path_threads_tool_display_config_to_telegram() -> None:
    """wire_telegram_adapters must call adapter.configure_tool_display() post-build.

    #1468 removed tool_display_config= from TelegramAdapter.__init__. The wiring
    contract is now: construct without the kwarg, then call configure_tool_display()
    on the returned instance.
    """
    from lyra.bootstrap.wiring.bootstrap_wiring import (
        TelegramWiringDeps,
        wire_telegram_adapters,
    )
    from lyra.config import TelegramBotConfig
    from lyra.core.auth.authenticator import Authenticator
    from lyra.core.auth.trust import TrustLevel
    from lyra.core.circuit_breaker import CircuitRegistry
    from lyra.core.hub.hub import Hub

    raw_config = {"tool_display": {"bash_max_len": 200, "show": {"web_fetch": False}}}

    # Arrange — loader parses the section correctly
    tool_display_cfg = _load_tool_display_config(raw_config)
    assert tool_display_cfg.bash_max_len == 200
    assert tool_display_cfg.show["web_fetch"] is False

    hub = Hub()
    bot_cfg = TelegramBotConfig(bot_id="main")
    auth = Authenticator(store=None, role_map={}, default=TrustLevel.PUBLIC)

    captured_constructor_kwargs: dict = {}
    captured_adapter_instance: MagicMock | None = None

    def _capture_adapter(**kwargs):
        captured_constructor_kwargs.update(kwargs)
        mock = MagicMock()
        mock.resolve_identity = AsyncMock()
        nonlocal captured_adapter_instance
        captured_adapter_instance = mock
        return mock

    with (
        patch(
            "lyra.bootstrap.wiring.bootstrap_wiring.TelegramAdapter",
            side_effect=_capture_adapter,
        ),
        patch(
            "lyra.bootstrap.credentials.load_bot_token",
            return_value=("fake-token", None),
        ),
    ):
        await wire_telegram_adapters(
            TelegramWiringDeps(
                hub=hub,
                tg_bot_auths=[(bot_cfg, auth)],
                bot_agent_map={("telegram", "main"): "lyra_default"},
                circuit_registry=CircuitRegistry(),
                msg_manager=MagicMock(),
                tool_display_config=tool_display_cfg,
            )
        )

    # #1468 contract: constructor must NOT receive tool_display_config= kwarg.
    assert "tool_display_config" not in captured_constructor_kwargs, (
        "TelegramAdapter constructor must NOT receive tool_display_config= "
        "after #1468 (setter replaces kwarg)"
    )

    # Post-construction setter must be called with the correct config.
    assert captured_adapter_instance is not None
    captured_adapter_instance.configure_tool_display.assert_called_once_with(
        tool_display_cfg
    )


# ---------------------------------------------------------------------------
# Test 2: wired path — Discord
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wired_path_threads_tool_display_config_to_discord() -> None:
    """wire_discord_adapters must call adapter.configure_tool_display() post-build.

    #1468 removed tool_display_config= from DiscordAdapter.__init__. The wiring
    contract is now: construct without the kwarg, then call configure_tool_display()
    on the returned instance.
    """
    from lyra.bootstrap.wiring.bootstrap_wiring import (
        DiscordWiringDeps,
        wire_discord_adapters,
    )
    from lyra.config import DiscordBotConfig
    from lyra.core.auth.authenticator import Authenticator
    from lyra.core.auth.trust import TrustLevel
    from lyra.core.circuit_breaker import CircuitRegistry
    from lyra.core.hub.hub import Hub

    # Confirm loader parses correctly — the adapter must receive equivalent values.
    loader_result = _load_tool_display_config(
        {"tool_display": {"bash_max_len": 200, "show": {"web_fetch": False}}}
    )
    assert loader_result.bash_max_len == 200

    hub = Hub()
    bot_cfg = DiscordBotConfig(bot_id="main", auto_thread=False, thread_hot_hours=4)
    auth = Authenticator(store=None, role_map={}, default=TrustLevel.PUBLIC)

    captured_constructor_kwargs: dict = {}
    captured_adapter_instance: MagicMock | None = None

    def _capture_discord_adapter(**kwargs):
        captured_constructor_kwargs.update(kwargs)
        mock = MagicMock()
        mock._resolve_identity_fn = None
        nonlocal captured_adapter_instance
        captured_adapter_instance = mock
        return mock

    mock_thread_store = AsyncMock()
    mock_agent_store = MagicMock()
    mock_agent_store.get_bot_settings = MagicMock(return_value={})

    with (
        patch(
            "lyra.bootstrap.wiring.bootstrap_wiring.DiscordAdapter",
            side_effect=_capture_discord_adapter,
        ),
        patch(
            "lyra.bootstrap.credentials.load_bot_token",
            return_value=("dc-token", None),
        ),
        patch(
            "lyra.infrastructure.stores.thread_store.ThreadStore",
            return_value=mock_thread_store,
        ),
    ):
        await wire_discord_adapters(
            DiscordWiringDeps(
                hub=hub,
                dc_bot_auths=[(bot_cfg, auth)],
                bot_agent_map={("discord", "main"): "lyra_default"},
                circuit_registry=CircuitRegistry(),
                msg_manager=MagicMock(),
                agent_store=mock_agent_store,
                tool_display_config=loader_result,
            )
        )

    # #1468 contract: constructor must NOT receive tool_display_config= kwarg.
    assert "tool_display_config" not in captured_constructor_kwargs, (
        "DiscordAdapter constructor must NOT receive tool_display_config= "
        "after #1468 (setter replaces kwarg)"
    )

    # Post-construction setter must be called with the correct config.
    assert captured_adapter_instance is not None
    captured_adapter_instance.configure_tool_display.assert_called_once_with(
        loader_result
    )


# ---------------------------------------------------------------------------
# Test 3: absent [tool_display] section → defaults (SC-5, integration level)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wired_path_with_absent_tool_display_section_uses_defaults() -> None:
    """raw_config={} (no [tool_display]) → configure_tool_display() receives defaults.

    SC-5: integration-level default parity — absent section must produce
    ToolDisplayConfig() defaults (bash_max_len=80) threaded through to the adapter
    via configure_tool_display(), not None.

    When tool_display_config is None in TelegramWiringDeps (absent section),
    configure_tool_display(None) is called and send_streaming falls back to
    ToolDisplayConfig() defaults — both paths are acceptable. This test verifies
    the wiring_helpers path which passes a default ToolDisplayConfig(), not None.
    """
    from lyra.bootstrap.wiring.bootstrap_wiring import (
        TelegramWiringDeps,
        wire_telegram_adapters,
    )
    from lyra.config import TelegramBotConfig
    from lyra.core.auth.authenticator import Authenticator
    from lyra.core.auth.trust import TrustLevel
    from lyra.core.circuit_breaker import CircuitRegistry
    from lyra.core.hub.hub import Hub

    # No [tool_display] section — loader returns defaults
    tool_display_cfg = _load_tool_display_config({})
    assert tool_display_cfg.bash_max_len == 80  # default

    hub = Hub()
    bot_cfg = TelegramBotConfig(bot_id="main")
    auth = Authenticator(store=None, role_map={}, default=TrustLevel.PUBLIC)

    captured_constructor_kwargs: dict = {}
    captured_adapter_instance: MagicMock | None = None

    def _capture_adapter(**kwargs):
        captured_constructor_kwargs.update(kwargs)
        mock = MagicMock()
        mock.resolve_identity = AsyncMock()
        nonlocal captured_adapter_instance
        captured_adapter_instance = mock
        return mock

    with (
        patch(
            "lyra.bootstrap.wiring.bootstrap_wiring.TelegramAdapter",
            side_effect=_capture_adapter,
        ),
        patch(
            "lyra.bootstrap.credentials.load_bot_token",
            return_value=("fake-token", None),
        ),
    ):
        await wire_telegram_adapters(
            TelegramWiringDeps(
                hub=hub,
                tg_bot_auths=[(bot_cfg, auth)],
                bot_agent_map={("telegram", "main"): "lyra_default"},
                circuit_registry=CircuitRegistry(),
                msg_manager=MagicMock(),
                tool_display_config=tool_display_cfg,
            )
        )

    # #1468 contract: constructor must NOT receive tool_display_config= kwarg.
    assert "tool_display_config" not in captured_constructor_kwargs, (
        "TelegramAdapter constructor must NOT receive tool_display_config= "
        "after #1468 (setter replaces kwarg)"
    )

    # configure_tool_display() must be called with the default config.
    assert captured_adapter_instance is not None
    captured_adapter_instance.configure_tool_display.assert_called_once()
    call_arg = captured_adapter_instance.configure_tool_display.call_args[0][0]
    assert call_arg.bash_max_len == 80, (
        f"Expected default bash_max_len=80, got {call_arg.bash_max_len}"
    )


# ---------------------------------------------------------------------------
# Test 4: standalone path — Telegram (SC-6)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_standalone_path_threads_tool_display_config_to_telegram() -> None:
    """Standalone bootstrap must call adapter.configure_tool_display() post-construct.

    #1468 removed tool_display_config= from TelegramAdapter.__init__. The standalone
    bootstrap contract is now: construct without the kwarg, then call
    configure_tool_display() on the returned instance with the parsed config.
    """
    from lyra.bootstrap.standalone.adapter_standalone import (
        _bootstrap_adapter_standalone,
    )

    raw_config = _tg_raw_config(
        tool_display={"bash_max_len": 200, "show": {"web_fetch": False}}
    )

    stop = asyncio.Event()
    stop.set()  # return immediately

    mock_nc = AsyncMock()
    mock_nc.subscribe = AsyncMock(return_value=AsyncMock())

    captured_constructor_kwargs: dict = {}
    captured_tg_adapter_instance: MagicMock | None = None

    def _capture_tg_adapter(**kwargs):
        captured_constructor_kwargs.update(kwargs)
        mock = MagicMock()
        mock._bot_id = "main"
        mock.resolve_identity = AsyncMock()
        mock.astart = AsyncMock()
        mock.close = AsyncMock()
        mock.dp = MagicMock()
        mock.dp.start_polling = AsyncMock(return_value=None)
        mock.dp.stop_polling = AsyncMock(return_value=None)
        mock._typing = MagicMock()
        nonlocal captured_tg_adapter_instance
        captured_tg_adapter_instance = mock
        return mock

    mock_inbound_bus = AsyncMock()
    mock_inbound_bus.register = MagicMock()
    mock_inbound_bus.start = AsyncMock()
    mock_inbound_bus.stop = AsyncMock()

    mock_listener = AsyncMock()
    mock_tg_typing_listener = AsyncMock()
    mock_turn_store = AsyncMock()

    with (
        patch("nats.connect", AsyncMock(return_value=mock_nc)),
        patch("lyra.nats.nats_bus.NatsBus", return_value=mock_inbound_bus),
        patch(
            "lyra.adapters.telegram.TelegramAdapter",
            side_effect=_capture_tg_adapter,
        ),
        patch(
            "lyra.bootstrap.wiring.standalone_telegram.NatsOutboundListener",
            return_value=mock_listener,
        ),
        patch(
            "lyra.bootstrap.wiring.standalone_telegram.wait_for_hub",
            AsyncMock(return_value=True),
        ),
        patch(
            "lyra.bootstrap.credentials.load_bot_token",
            return_value=("test-token", None),
        ),
        patch(
            "lyra.infrastructure.stores.turn_store.TurnStore",
            return_value=mock_turn_store,
        ),
        patch(
            "lyra.adapters.telegram.telegram._telegram_scope_resolver",
            MagicMock(),
        ),
        patch(
            "lyra.adapters.telegram.telegram_outbound._typing_worker",
            MagicMock(),
        ),
        patch(
            "lyra.typing.TypingListener",
            return_value=mock_tg_typing_listener,
        ),
        patch(
            "lyra.typing.make_typing_factory",
            return_value=MagicMock(),
        ),
        patch(
            "lyra.bootstrap.standalone.adapter_standalone.start_audio_consumer",
            return_value=AsyncMock(),
        ),
        patch.dict(os.environ, {"NATS_URL": "nats://localhost:4222"}),
    ):
        await _bootstrap_adapter_standalone(raw_config, "telegram", _stop=stop)

    # #1468 contract: constructor must NOT receive tool_display_config= kwarg.
    assert "tool_display_config" not in captured_constructor_kwargs, (
        "TelegramAdapter constructor must NOT receive tool_display_config= "
        "after #1468 (setter replaces kwarg)"
    )

    # configure_tool_display() must be called post-construction with the parsed config.
    assert captured_tg_adapter_instance is not None
    captured_tg_adapter_instance.configure_tool_display.assert_called_once()
    adapter_cfg = captured_tg_adapter_instance.configure_tool_display.call_args[0][0]
    assert adapter_cfg.bash_max_len == 200
    assert adapter_cfg.show["web_fetch"] is False


@pytest.mark.asyncio
async def test_standalone_path_threads_tool_display_config_to_discord() -> None:
    """Standalone bootstrap must call Discord configure_tool_display() post-construct.

    Symmetric to the Telegram standalone test. #1468 removed tool_display_config=
    from DiscordAdapter.__init__; the standalone bootstrap must call the setter
    after construction (adapter_standalone.py callsite).
    """
    from lyra.bootstrap.standalone.adapter_standalone import (
        _bootstrap_adapter_standalone,
    )

    raw_config = _dc_raw_config(
        tool_display={"bash_max_len": 200, "show": {"web_fetch": False}}
    )

    stop = asyncio.Event()
    stop.set()  # return immediately

    mock_nc = AsyncMock()
    mock_nc.subscribe = AsyncMock(return_value=AsyncMock())

    captured_constructor_kwargs_dc: dict = {}
    captured_dc_adapter_instance: MagicMock | None = None

    def _capture_dc_adapter(**kwargs):
        captured_constructor_kwargs_dc.update(kwargs)
        mock = MagicMock()
        mock._bot_id = "main"
        mock.resolve_identity = AsyncMock()
        mock.astart = AsyncMock()
        mock.start = AsyncMock()
        mock.close = AsyncMock()
        mock._typing = MagicMock()
        mock._resolve_identity_fn = None
        mock._resolve_channel = MagicMock()
        nonlocal captured_dc_adapter_instance
        captured_dc_adapter_instance = mock
        return mock

    mock_inbound_bus = AsyncMock()
    mock_inbound_bus.register = MagicMock()
    mock_inbound_bus.start = AsyncMock()
    mock_inbound_bus.stop = AsyncMock()

    mock_listener = AsyncMock()
    mock_dc_typing_listener = AsyncMock()
    mock_thread_store = AsyncMock()
    mock_turn_store = AsyncMock()

    with (
        patch("nats.connect", AsyncMock(return_value=mock_nc)),
        patch("lyra.nats.nats_bus.NatsBus", return_value=mock_inbound_bus),
        patch(
            "lyra.adapters.discord.DiscordAdapter",
            side_effect=_capture_dc_adapter,
        ),
        patch(
            "lyra.bootstrap.wiring.standalone_discord.NatsOutboundListener",
            return_value=mock_listener,
        ),
        patch(
            "lyra.bootstrap.wiring.standalone_discord.wait_for_hub",
            AsyncMock(return_value=True),
        ),
        patch(
            "lyra.bootstrap.credentials.load_bot_token",
            return_value=("test-token", None),
        ),
        patch(
            "lyra.infrastructure.stores.thread_store.ThreadStore",
            return_value=mock_thread_store,
        ),
        patch(
            "lyra.infrastructure.stores.turn_store.TurnStore",
            return_value=mock_turn_store,
        ),
        patch(
            "lyra.typing.TypingListener",
            return_value=mock_dc_typing_listener,
        ),
        patch(
            "lyra.typing.make_typing_factory",
            return_value=MagicMock(),
        ),
        patch(
            "lyra.bootstrap.standalone.adapter_standalone.start_audio_consumer",
            return_value=AsyncMock(),
        ),
        patch.dict(os.environ, {"NATS_URL": "nats://localhost:4222"}),
    ):
        await _bootstrap_adapter_standalone(raw_config, "discord", _stop=stop)

    # #1468 contract: constructor must NOT receive tool_display_config= kwarg.
    assert "tool_display_config" not in captured_constructor_kwargs_dc, (
        "DiscordAdapter constructor must NOT receive tool_display_config= "
        "after #1468 (setter replaces kwarg)"
    )

    # configure_tool_display() must be called post-construction with the parsed config.
    assert captured_dc_adapter_instance is not None
    captured_dc_adapter_instance.configure_tool_display.assert_called_once()
    adapter_cfg = captured_dc_adapter_instance.configure_tool_display.call_args[0][0]
    assert adapter_cfg.bash_max_len == 200
    assert adapter_cfg.show["web_fetch"] is False
