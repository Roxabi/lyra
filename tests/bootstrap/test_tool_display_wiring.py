"""Slice 3 RED tests — tool_display_config threading from raw_config to adapters.

Tests assert that the [tool_display] section in raw_config is loaded and threaded
through both the wired bootstrap path (bootstrap_wiring.py) and the standalone
adapter path (adapter_standalone.py) to TelegramAdapter._tool_display_config and
DiscordAdapter._tool_display_config.

These tests are intentionally RED until T20 (wired path) + T21 (standalone path)
implement the loader call + kwarg threading in the respective bootstrap modules.

SC-5: absent [tool_display] section → ToolDisplayConfig() defaults (integration level).
SC-6: all 4 bootstrap callsites pass tool_display_config= to adapter constructors.
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
    """wire_telegram_adapters must pass tool_display_config= to TelegramAdapter.

    RED trigger: wire_telegram_adapters currently does NOT pass tool_display_config=.
    T20 implements the wiring at bootstrap_wiring.py L64.
    """
    from lyra.bootstrap.wiring.bootstrap_wiring import wire_telegram_adapters
    from lyra.config import TelegramBotConfig
    from lyra.core.auth.authenticator import Authenticator
    from lyra.core.auth.trust import TrustLevel
    from lyra.core.circuit_breaker import CircuitRegistry
    from lyra.core.hub.hub import Hub

    raw_config = {"tool_display": {"bash_max_len": 200, "show": {"web_fetch": False}}}

    # Arrange — loader parses the section correctly (loader itself is already done)
    tool_display_cfg = _load_tool_display_config(raw_config)
    assert tool_display_cfg.bash_max_len == 200
    assert tool_display_cfg.show["web_fetch"] is False

    hub = Hub()
    bot_cfg = TelegramBotConfig(bot_id="main")
    auth = Authenticator(store=None, role_map={}, default=TrustLevel.PUBLIC)

    captured_kwargs: dict = {}

    def _capture_adapter(**kwargs):
        captured_kwargs.update(kwargs)
        mock = MagicMock()
        mock.resolve_identity = AsyncMock()
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
            hub=hub,
            tg_bot_auths=[(bot_cfg, auth)],
            bot_agent_map={("telegram", "main"): "lyra_default"},
            circuit_registry=CircuitRegistry(),
            msg_manager=MagicMock(),
            tool_display_config=tool_display_cfg,
        )

    # T20 contract: wire_telegram_adapters threads tool_display_config kwarg
    # through to the TelegramAdapter constructor. Loader call lives one layer
    # up in wiring_helpers._wire_adapters (where raw_config is in scope).
    assert "tool_display_config" in captured_kwargs, (
        "wire_telegram_adapters must forward tool_display_config= to "
        "TelegramAdapter constructor"
    )
    assert captured_kwargs["tool_display_config"].bash_max_len == 200
    assert captured_kwargs["tool_display_config"].show["web_fetch"] is False


# ---------------------------------------------------------------------------
# Test 2: wired path — Discord
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wired_path_threads_tool_display_config_to_discord() -> None:
    """wire_discord_adapters must pass tool_display_config= to DiscordAdapter.

    RED until T20 adds the kwarg to the DiscordAdapter callsite in
    bootstrap_wiring.py (L170).
    """
    from lyra.bootstrap.wiring.bootstrap_wiring import wire_discord_adapters
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

    captured_kwargs: dict = {}

    def _capture_discord_adapter(**kwargs):
        captured_kwargs.update(kwargs)
        mock = MagicMock()
        mock._resolve_identity_fn = None
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
            hub=hub,
            dc_bot_auths=[(bot_cfg, auth)],
            bot_agent_map={("discord", "main"): "lyra_default"},
            circuit_registry=CircuitRegistry(),
            msg_manager=MagicMock(),
            agent_store=mock_agent_store,
            tool_display_config=loader_result,
        )

    # T20 contract: wire_discord_adapters threads tool_display_config kwarg
    # through to the DiscordAdapter constructor. Loader call lives one layer
    # up in wiring_helpers._wire_adapters (where raw_config is in scope).
    assert "tool_display_config" in captured_kwargs, (
        "wire_discord_adapters must forward tool_display_config= to "
        "DiscordAdapter constructor"
    )
    assert captured_kwargs["tool_display_config"].bash_max_len == 200
    assert captured_kwargs["tool_display_config"].show["web_fetch"] is False


# ---------------------------------------------------------------------------
# Test 3: absent [tool_display] section → defaults (SC-5, integration level)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wired_path_with_absent_tool_display_section_uses_defaults() -> None:
    """raw_config={} (no [tool_display]) → TelegramAdapter receives default config.

    SC-5: integration-level default parity — absent section must produce
    ToolDisplayConfig() defaults (bash_max_len=80, names_threshold=5) threaded
    through to the adapter, not None.

    RED until T20 wires the loader into wire_telegram_adapters so the kwarg is
    always populated.
    """
    from lyra.bootstrap.wiring.bootstrap_wiring import wire_telegram_adapters
    from lyra.config import TelegramBotConfig
    from lyra.core.auth.authenticator import Authenticator
    from lyra.core.auth.trust import TrustLevel
    from lyra.core.circuit_breaker import CircuitRegistry
    from lyra.core.hub.hub import Hub

    # No [tool_display] section
    tool_display_cfg = _load_tool_display_config({})
    assert tool_display_cfg.bash_max_len == 80  # default after Slice 1

    hub = Hub()
    bot_cfg = TelegramBotConfig(bot_id="main")
    auth = Authenticator(store=None, role_map={}, default=TrustLevel.PUBLIC)

    captured_kwargs: dict = {}

    def _capture_adapter(**kwargs):
        captured_kwargs.update(kwargs)
        mock = MagicMock()
        mock.resolve_identity = AsyncMock()
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
            hub=hub,
            tg_bot_auths=[(bot_cfg, auth)],
            bot_agent_map={("telegram", "main"): "lyra_default"},
            circuit_registry=CircuitRegistry(),
            msg_manager=MagicMock(),
        )

    # RED: T20 must thread tool_display_cfg (default) to TelegramAdapter.
    # Until then: key absent → KeyError / assertion fails.
    assert "tool_display_config" in captured_kwargs, (
        "RED: wire_telegram_adapters does not pass tool_display_config= "
        "when [tool_display] section is absent (T20 implements this)"
    )
    adapter_cfg = captured_kwargs["tool_display_config"]
    assert adapter_cfg.bash_max_len == 80, (
        f"Expected default bash_max_len=80, got {adapter_cfg.bash_max_len}"
    )


# ---------------------------------------------------------------------------
# Test 4: standalone path — Telegram (SC-6)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_standalone_path_threads_tool_display_config_to_telegram() -> None:
    """_bootstrap_adapter_standalone must pass tool_display_config= to TelegramAdapter.

    RED until T21 adds the _load_tool_display_config call + kwarg threading in
    adapter_standalone.py (L101 callsite).
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

    captured_kwargs: dict = {}

    def _capture_tg_adapter(**kwargs):
        captured_kwargs.update(kwargs)
        mock = MagicMock()
        mock._bot_id = "main"
        mock.resolve_identity = AsyncMock()
        mock.astart = AsyncMock()
        mock.close = AsyncMock()
        mock.dp = MagicMock()
        mock.dp.start_polling = AsyncMock(return_value=None)
        mock.dp.stop_polling = AsyncMock(return_value=None)
        mock._typing = MagicMock()
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
            "lyra.bootstrap.standalone.adapter_standalone.NatsOutboundListener",
            return_value=mock_listener,
        ),
        patch(
            "lyra.bootstrap.standalone.adapter_standalone.wait_for_hub",
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
        patch.dict(os.environ, {"NATS_URL": "nats://localhost:4222"}),
    ):
        await _bootstrap_adapter_standalone(raw_config, "telegram", _stop=stop)

    # RED: T21 must add _load_tool_display_config(raw_config) call and pass
    # tool_display_config= kwarg to TelegramAdapter at L101 in adapter_standalone.py.
    assert "tool_display_config" in captured_kwargs, (
        "RED: _bootstrap_adapter_standalone does not yet pass tool_display_config= "
        "to TelegramAdapter constructor (T21 implements this)"
    )
    adapter_cfg = captured_kwargs["tool_display_config"]
    assert adapter_cfg.bash_max_len == 200
    assert adapter_cfg.show["web_fetch"] is False


@pytest.mark.asyncio
async def test_standalone_path_threads_tool_display_config_to_discord() -> None:
    """_bootstrap_adapter_standalone must pass tool_display_config= to DiscordAdapter.

    Symmetric to the Telegram standalone test; closes the SC-6 coverage gap on
    the Discord standalone bootstrap callsite (adapter_standalone.py:269).
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

    captured_kwargs: dict = {}

    def _capture_dc_adapter(**kwargs):
        captured_kwargs.update(kwargs)
        mock = MagicMock()
        mock._bot_id = "main"
        mock.resolve_identity = AsyncMock()
        mock.astart = AsyncMock()
        mock.start = AsyncMock()
        mock.close = AsyncMock()
        mock._typing = MagicMock()
        mock._resolve_identity_fn = None
        mock._resolve_channel = MagicMock()
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
            "lyra.bootstrap.standalone.adapter_standalone.NatsOutboundListener",
            return_value=mock_listener,
        ),
        patch(
            "lyra.bootstrap.standalone.adapter_standalone.wait_for_hub",
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
        patch.dict(os.environ, {"NATS_URL": "nats://localhost:4222"}),
    ):
        await _bootstrap_adapter_standalone(raw_config, "discord", _stop=stop)

    assert "tool_display_config" in captured_kwargs, (
        "_bootstrap_adapter_standalone must pass tool_display_config= "
        "to DiscordAdapter constructor (SC-6 standalone half)"
    )
    adapter_cfg = captured_kwargs["tool_display_config"]
    assert adapter_cfg.bash_max_len == 200
    assert adapter_cfg.show["web_fetch"] is False
