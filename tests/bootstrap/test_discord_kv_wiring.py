"""Integration tests for Discord KV wiring — SC1/SC6.

Tests verify:
  1. No AgentStore / config.db read in _bootstrap_discord_setup (SC1).
  2. Discord adapter receives watch_channels seeded from KV (SC1 / _wire_bot).
  3. publish_watch_channels is awaited before announce_hub_ready in hub (SC6).
"""

from __future__ import annotations

import asyncio
import inspect
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Assertion 1 — No AgentStore / config.db in _bootstrap_discord_setup
# ---------------------------------------------------------------------------


class TestDiscordSetupNoAgentStore:
    """SC1: _bootstrap_discord_setup must NOT touch AgentStore or config.db."""

    async def test_setup_completes_without_constructing_agent_store(self) -> None:
        """_bootstrap_discord_setup runs to completion without AgentStore.

        Behavioral approach: patch AgentStore.__init__ to raise AssertionError
        so any instantiation fails loudly, then drive the function and assert it
        returns (dc_multi_cfg, dc_creds) without raising.

        Belt-and-suspenders: also assert source-level absence of both
        'config.db' and 'AgentStore' in the standalone_discord module.
        """
        # Arrange
        from factory.bootstrap.wiring import standalone_discord as _mod

        raw_config = {
            "discord": {
                "bots": [
                    {
                        "bot_id": "testbot",
                        "auto_thread": False,
                        "thread_hot_hours": 4,
                    }
                ]
            }
        }

        with (
            patch(
                "factory.infrastructure.stores.agent_store.AgentStore.__init__",
                side_effect=AssertionError("config.db read!"),
            ),
            patch(
                "factory.bootstrap.credentials.load_bot_token",
                return_value=("fake-token", None),
            ),
        ):
            # Act — must not raise
            result = await _mod._bootstrap_discord_setup(raw_config)

        # Assert — returns (dc_multi_cfg, dc_creds) tuple without raising
        dc_multi_cfg, dc_creds = result
        assert dc_multi_cfg.bots[0].bot_id == "testbot"
        assert dc_creds == {"testbot": "fake-token"}

        # Belt-and-suspenders: source-level absence guarantees
        module_source = inspect.getsource(_mod)
        assert "config.db" not in module_source, (
            "standalone_discord references 'config.db' — must not read config DB"
        )
        assert "AgentStore" not in module_source, (
            "standalone_discord imports or uses AgentStore — must be KV-only"
        )

    def test_agent_store_absent_from_standalone_discord_source(self) -> None:
        """Source-level guard: AgentStore import must be absent (SC1).

        Negative test: deleting the KV-wiring and re-adding AgentStore would
        make this test RED immediately.
        """
        # Arrange
        from factory.bootstrap.wiring import standalone_discord as _mod

        # Act
        source = inspect.getsource(_mod)

        # Assert
        assert "AgentStore" not in source
        assert "config.db" not in source


# ---------------------------------------------------------------------------
# Assertion 2 — _wire_bot receives watch_channels from KV seed
# ---------------------------------------------------------------------------


class TestDiscordWireBotReceivesWatchChannels:
    """SC1 / _wire_bot: DiscordAdapter must receive watch_channels from KV seed."""

    async def test_wire_bot_forwards_seeded_watch_channels_to_adapter(self) -> None:
        """seed_watch_channels returns {42, 99} → DiscordAdapter gets those IDs.

        Tests _wire_bot indirectly via bootstrap_discord_standalone wiring loop.
        Heavy deps (NATS, stores, wire_bot_common, etc.) are all mocked.
        The key assertion is on the DiscordAdapter constructor call kwargs.
        """
        # Arrange
        from factory.bootstrap.wiring.standalone_discord import (
            bootstrap_discord_standalone,
        )

        stop = asyncio.Event()
        stop.set()

        raw_config = {
            "discord": {
                "bots": [
                    {
                        "bot_id": "testbot",
                        "auto_thread": False,
                        "thread_hot_hours": 4,
                    }
                ]
            }
        }
        from factory.bootstrap.factory.config import AdapterConfigBundle
        from factory.core.messaging.message import Platform

        config_bundle = MagicMock(spec=AdapterConfigBundle)

        mock_nc = AsyncMock()
        mock_js = MagicMock()
        mock_nc.jetstream = MagicMock(return_value=mock_js)

        # Thread store and turn store stubs
        mock_thread_store = AsyncMock()
        mock_thread_store.connect = AsyncMock()
        mock_thread_store.close = AsyncMock()
        mock_turn_store = AsyncMock()
        mock_turn_store.connect = AsyncMock()
        mock_turn_store.close = AsyncMock()

        # DiscordAdapter stub — capture constructor kwargs
        mock_adapter = MagicMock()
        mock_adapter._bot_id = "testbot"
        mock_adapter.close = AsyncMock()
        mock_adapter.start = AsyncMock()
        mock_adapter._watch_channels = frozenset()
        mock_adapter._resolve_channel = MagicMock()

        captured_kwargs: dict = {}

        def _make_discord_adapter(**kwargs):
            captured_kwargs.update(kwargs)
            return mock_adapter

        # wire_bot_common stub returns (adapter, inbound_bus, typing_listener, consumer)
        mock_inbound_bus = AsyncMock()
        mock_inbound_bus.stop = AsyncMock()
        mock_typing_listener = AsyncMock()
        mock_typing_listener.stop = AsyncMock()
        mock_consumer = AsyncMock()
        mock_consumer.stop = AsyncMock()

        seeded_channels = frozenset({42, 99})

        # wire_bot_common stub: invoke the adapter_factory to exercise the
        # _dc_adapter_factory closure (which calls DiscordAdapter with watch_channels).
        async def _fake_wire_bot_common(
            *,
            adapter_factory,
            **_kwargs,
        ):
            mock_inbound_bus_arg = AsyncMock()
            mock_inbound_bus_arg.stop = AsyncMock()
            # Calling adapter_factory triggers _dc_adapter_factory(inbound_bus)
            # which constructs DiscordAdapter(watch_channels=seeded_channels, ...)
            adapter_factory(mock_inbound_bus_arg)
            return (
                mock_adapter,
                mock_inbound_bus,
                mock_typing_listener,
                mock_consumer,
            )

        # _bootstrap_discord_teardown needs start_tasks so patch out stop_dc
        with (
            patch(
                "factory.bootstrap.credentials.load_bot_token",
                return_value=("fake-token", None),
            ),
            patch(
                "factory.bootstrap.wiring.standalone_discord._create_dc_stores",
                AsyncMock(return_value=(mock_thread_store,)),
            ),
            patch(
                "factory.bootstrap.wiring.standalone_discord.init_blobstore",
                return_value=None,
            ),
            patch(
                "factory.bootstrap.wiring.standalone_discord.wait_for_hub",
                AsyncMock(return_value=None),
            ),
            patch(
                "factory.bootstrap.wiring.standalone_discord.seed_watch_channels",
                AsyncMock(return_value=seeded_channels),
            ),
            patch(
                "factory.bootstrap.wiring.standalone_discord.wire_bot_common",
                side_effect=_fake_wire_bot_common,
            ),
            patch(
                "factory.adapters.discord.DiscordAdapter",
                side_effect=_make_discord_adapter,
            ),
            patch(
                "factory.bootstrap.lifecycle.signal_handlers.setup_shutdown_event",
                return_value=stop,
            ),
            patch(
                "factory.bootstrap.lifecycle.lifecycle_helpers.close_safely",
                AsyncMock(),
            ),
        ):
            await bootstrap_discord_standalone(
                nc=mock_nc,
                raw_config=raw_config,
                config_bundle=config_bundle,
                platform_enum=Platform.DISCORD,
                _stop=stop,
            )

        # Assert — DiscordAdapter instantiated with exact seeded channel set
        assert "watch_channels" in captured_kwargs, (
            "DiscordAdapter not constructed with watch_channels kwarg"
        )
        assert captured_kwargs["watch_channels"] == seeded_channels, (
            f"Expected watch_channels={seeded_channels!r}, "
            f"got {captured_kwargs['watch_channels']!r}"
        )


# ---------------------------------------------------------------------------
# Assertion 3 — Hub publishes watch_channels before announce_hub_ready (SC6)
# ---------------------------------------------------------------------------


def _make_hub_stubs() -> tuple:
    """Return (mock_nc, fake_open_stores) for hub bootstrap short-circuit tests."""
    mock_nc = AsyncMock()
    mock_nc.is_connected = True
    mock_nc.close = AsyncMock()
    mock_nc.drain = AsyncMock()
    mock_js = MagicMock()
    mock_nc.jetstream = MagicMock(return_value=mock_js)

    @asynccontextmanager
    async def _fake_open_stores(*_args, **_kwargs):
        stores = MagicMock()
        stores.message_index.cleanup_older_than = AsyncMock(return_value=0)
        stores.auth = MagicMock()
        stores.bot = MagicMock()
        stores.identity_alias = MagicMock()
        stores.agent = MagicMock()
        yield stores

    return mock_nc, _fake_open_stores


def _hub_test_config() -> dict:
    return {
        "defaults": {"cwd": "/tmp"},
        "admin": {"user_ids": ["test_admin"]},
        "telegram": {"bots": []},
        "discord": {"bots": []},
        "auth": {"telegram_bots": [], "discord_bots": []},
        "message_index": {},
    }


class TestHubPublishesWatchChannelsBeforeReady:
    """SC6: publish_watch_channels must be awaited before announce_hub_ready."""

    def test_publish_watch_channels_before_announce_hub_ready_source_order(
        self,
    ) -> None:
        """AST guard: publish_watch_channels precedes announce_hub_ready in source.

        Structural assertion complementing the behavioral test below.
        """
        import ast

        import factory.bootstrap.standalone.hub_standalone as _mod

        func_source = inspect.getsource(_mod._bootstrap_hub_standalone)
        tree = ast.parse(func_source)
        positions: dict[str, int] = {
            "publish_watch_channels": -1,
            "announce_hub_ready": -1,
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else (func.attr if isinstance(func, ast.Attribute) else None)
            )
            if name in positions and positions[name] == -1:
                positions[name] = node.lineno

        pub_line = positions["publish_watch_channels"]
        ready_line = positions["announce_hub_ready"]

        assert pub_line != -1, (
            "publish_watch_channels not found in hub_standalone — SC6 violated"
        )
        assert ready_line != -1, (
            "announce_hub_ready not found in hub_standalone — unexpected"
        )
        assert pub_line < ready_line, (
            f"publish_watch_channels (line {pub_line}) must precede "
            f"announce_hub_ready (line {ready_line}) — SC6 ordering violated"
        )

    async def test_publish_watch_channels_awaited_before_announce_hub_ready(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Behavioral: publish_watch_channels called before announce_hub_ready.

        Records the call order via side_effect callbacks and stops execution
        after announce_hub_ready to avoid running the full hub lifecycle.
        """
        # Arrange
        monkeypatch.setenv("NATS_URL", "nats://localhost:4222")
        raw_config = _hub_test_config()
        mock_nc, fake_open_stores = _make_hub_stubs()
        mock_nc.jetstream.return_value = MagicMock(
            add_stream=AsyncMock(), update_stream=AsyncMock()
        )

        call_order: list[str] = []

        async def _record_publish(*_a, **_kw):
            call_order.append("publish_watch_channels")

        async def _record_announce(*_a, **_kw):
            call_order.append("announce_hub_ready")
            raise SystemExit("test-sentinel: stop after announce_hub_ready")

        from factory.bootstrap.standalone.hub_standalone import (
            _bootstrap_hub_standalone,
        )

        with (
            patch(
                "factory.bootstrap.standalone.hub_standalone.nats_connect",
                AsyncMock(return_value=mock_nc),
            ),
            patch(
                "factory.bootstrap.standalone.hub_standalone.acquire_lockfile",
            ),
            patch(
                "factory.bootstrap.standalone.hub_standalone.release_lockfile",
            ),
            patch(
                "factory.bootstrap.standalone.hub_standalone.open_stores",
                fake_open_stores,
            ),
            patch(
                "factory.bootstrap.standalone.hub_standalone.seed_grants_from_bots",
                AsyncMock(),
            ),
            patch(
                "factory.bootstrap.standalone.hub_standalone.build_bot_auths",
                return_value=(MagicMock(), [], [], []),
            ),
            patch(
                "factory.bootstrap.standalone.hub_standalone._resolve_bot_agent_map",
                AsyncMock(return_value={}),
            ),
            patch(
                "factory.bootstrap.standalone.hub_standalone.load_agent_configs",
                return_value={"default": MagicMock()},
            ),
            patch(
                "factory.bootstrap.factory.config._load_messages",
                return_value=MagicMock(),
            ),
            patch(
                "factory.bootstrap.standalone.hub_standalone.build_pairing_manager",
                AsyncMock(return_value=MagicMock()),
            ),
            patch(
                "factory.bootstrap.standalone.hub_standalone._build_hub_and_wire",
                AsyncMock(
                    return_value=(
                        MagicMock(
                            inbound_bus=AsyncMock(start=AsyncMock()),
                        ),
                        [],
                        [],
                        MagicMock(),
                        MagicMock(),
                    )
                ),
            ),
            patch(
                "factory.bootstrap.standalone.hub_standalone"
                ".start_mint_failure_subscriber",
                AsyncMock(return_value=MagicMock()),
            ),
            patch(
                "factory.infrastructure.outbound_audio.stream_setup.ensure_stream",
                AsyncMock(),
            ),
            patch(
                "factory.infrastructure.outbound_audio.stream_setup.ensure_kv",
                AsyncMock(return_value=MagicMock()),
            ),
            # publish_watch_channels is imported at module top-level — patch there
            patch(
                "factory.bootstrap.standalone.hub_standalone.publish_watch_channels",
                side_effect=_record_publish,
            ),
            patch(
                "factory.bootstrap.standalone.hub_standalone.announce_hub_ready",
                side_effect=_record_announce,
            ),
            patch(
                "factory.bootstrap.standalone.hub_standalone.log_contracts_version",
            ),
            patch(
                "factory.bootstrap.standalone.hub_standalone.build_inbound_bus",
                return_value=(AsyncMock(), MagicMock()),
            ),
        ):
            # Act
            with pytest.raises(SystemExit, match="test-sentinel"):
                await _bootstrap_hub_standalone(raw_config)

        # Assert — publish before announce
        assert "publish_watch_channels" in call_order, (
            "publish_watch_channels was never awaited — SC6 violated"
        )
        assert "announce_hub_ready" in call_order, (
            "announce_hub_ready was never called — unexpected"
        )
        pub_idx = call_order.index("publish_watch_channels")
        ready_idx = call_order.index("announce_hub_ready")
        assert pub_idx < ready_idx, (
            f"publish_watch_channels (pos {pub_idx}) must precede "
            f"announce_hub_ready (pos {ready_idx}) — SC6 ordering violated"
        )

