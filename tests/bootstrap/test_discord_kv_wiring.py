"""Integration tests for Discord KV wiring — SC1/SC6 + #1946 roster."""

from __future__ import annotations

import asyncio
import inspect
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestDiscordSetupUsesKvRoster:
    def test_no_bot_store_or_config_db_in_standalone_discord_source(self) -> None:
        from factory.bootstrap.wiring import standalone_discord as _mod

        source = inspect.getsource(_mod)
        assert "BotStore" not in source
        assert "config.db" not in source
        assert "seed_bot_roster" in source
        assert "AgentStore" not in source

    def test_no_bot_store_or_config_db_in_standalone_telegram_source(self) -> None:
        from factory.bootstrap.wiring import standalone_telegram as _mod

        source = inspect.getsource(_mod)
        assert "BotStore" not in source
        assert "config.db" not in source
        assert "seed_bot_roster" in source
        assert "AgentStore" not in source


class TestDiscordWireBotReceivesWatchChannels:
    @pytest.mark.asyncio
    async def test_wire_bot_forwards_seeded_watch_channels_to_adapter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from factory.bootstrap.factory.config import AdapterConfigBundle
        from factory.bootstrap.wiring.standalone_discord import (
            bootstrap_discord_standalone,
        )
        from factory.core.messaging.message import Platform
        from tests.helpers.standalone_bot_store import patch_discord_roster

        stop = asyncio.Event()
        stop.set()
        patch_discord_roster(
            monkeypatch,
            ["testbot"],
            auto_thread=False,
            thread_hot_hours=4,
        )

        mock_nc = AsyncMock()
        mock_nc.jetstream = MagicMock(return_value=MagicMock())
        mock_thread_store = AsyncMock()
        mock_thread_store.connect = AsyncMock()
        mock_thread_store.close = AsyncMock()

        mock_adapter = MagicMock()
        mock_adapter._bot_id = "testbot"
        mock_adapter.close = AsyncMock()
        mock_adapter.start = AsyncMock()

        captured_kwargs: dict = {}

        def _make_discord_adapter(**kwargs):
            captured_kwargs.update(kwargs)
            return mock_adapter

        seeded_channels = frozenset({42, 99})

        async def _fake_wire_bot_common(*, adapter_factory, **_kwargs):
            adapter_factory(AsyncMock())
            return (mock_adapter, AsyncMock(), AsyncMock(), AsyncMock())

        monkeypatch.setattr(
            "factory.bootstrap.credentials.load_bot_token",
            lambda *_a, **_kw: ("fake-token", None),
        )
        monkeypatch.setattr(
            "factory.bootstrap.wiring.standalone_discord._create_dc_stores",
            AsyncMock(return_value=(mock_thread_store,)),
        )
        monkeypatch.setattr(
            "factory.bootstrap.wiring.standalone_discord.init_blobstore",
            lambda: None,
        )
        monkeypatch.setattr(
            "factory.bootstrap.wiring.standalone_discord.wait_for_hub",
            AsyncMock(),
        )
        monkeypatch.setattr(
            "factory.bootstrap.wiring.standalone_discord.seed_watch_channels",
            AsyncMock(return_value=seeded_channels),
        )
        monkeypatch.setattr(
            "factory.bootstrap.wiring.standalone_discord.wire_bot_common",
            _fake_wire_bot_common,
        )
        monkeypatch.setattr(
            "factory.adapters.discord.DiscordAdapter",
            _make_discord_adapter,
        )
        monkeypatch.setattr(
            "factory.bootstrap.lifecycle.signal_handlers.setup_shutdown_event",
            lambda *_a, **_kw: stop,
        )
        monkeypatch.setattr(
            "factory.bootstrap.lifecycle.lifecycle_helpers.close_safely",
            AsyncMock(),
        )

        await bootstrap_discord_standalone(
            nc=mock_nc,
            raw_config={},
            config_bundle=MagicMock(spec=AdapterConfigBundle),
            platform_enum=Platform.DISCORD,
            _stop=stop,
        )

        assert captured_kwargs.get("watch_channels") == seeded_channels


def _make_hub_stubs() -> tuple:
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


class TestHubPublishesKvStateBeforeReady:
    def test_publish_helpers_before_announce_hub_ready_source_order(self) -> None:
        import ast

        import factory.bootstrap.standalone.hub_standalone as _mod

        tree = ast.parse(inspect.getsource(_mod._bootstrap_hub_standalone))
        positions = {
            "publish_watch_channels": -1,
            "publish_bot_roster": -1,
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

        assert all(pos != -1 for pos in positions.values())
        assert positions["publish_watch_channels"] < positions["publish_bot_roster"]
        assert positions["publish_bot_roster"] < positions["announce_hub_ready"]

    @pytest.mark.asyncio
    async def test_publish_helpers_awaited_before_announce_hub_ready(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from factory.bootstrap.standalone.hub_standalone import (
            _bootstrap_hub_standalone,
        )

        call_order: list[str] = []
        mock_nc, fake_open_stores = _make_hub_stubs()
        mock_nc.jetstream.return_value = MagicMock(
            add_stream=AsyncMock(), update_stream=AsyncMock()
        )

        monkeypatch.setenv("NATS_URL", "nats://localhost:4222")
        monkeypatch.setattr(
            "factory.infrastructure.stores.active_jobs_kv.ensure_active_jobs_kv",
            AsyncMock(return_value=MagicMock()),
        )
        monkeypatch.setattr(
            "factory.infrastructure.stores.active_jobs_kv.KvActiveJobsStore",
            MagicMock(return_value=MagicMock(connect=AsyncMock())),
        )
        monkeypatch.setattr(
            "factory.infrastructure.stores.active_jobs_refresher.RegistryCoordinator",
            MagicMock(return_value=MagicMock(start=MagicMock(), stop=AsyncMock())),
        )
        monkeypatch.setattr(
            "factory.bootstrap.standalone.hub_standalone.acquire_lockfile",
            lambda: None,
        )
        monkeypatch.setattr(
            "factory.bootstrap.standalone.hub_standalone.nats_connect",
            AsyncMock(return_value=mock_nc),
        )
        monkeypatch.setattr(
            "factory.bootstrap.standalone.hub_standalone.open_stores",
            fake_open_stores,
        )
        monkeypatch.setattr(
            "factory.bootstrap.standalone.hub_standalone.seed_grants_from_bots",
            AsyncMock(),
        )
        monkeypatch.setattr(
            "factory.bootstrap.standalone.hub_standalone.build_bot_auths",
            lambda *_a, **_kw: (MagicMock(), [], [], []),
        )
        monkeypatch.setattr(
            "factory.bootstrap.standalone.hub_standalone._resolve_bot_agent_map",
            AsyncMock(return_value={}),
        )
        monkeypatch.setattr(
            "factory.bootstrap.standalone.hub_standalone.load_agent_configs",
            lambda *_a, **_kw: {"default": MagicMock()},
        )
        monkeypatch.setattr(
            "factory.bootstrap.factory.config._load_messages",
            lambda *_a, **_kw: MagicMock(),
        )
        monkeypatch.setattr(
            "factory.bootstrap.standalone.hub_standalone.build_pairing_manager",
            AsyncMock(return_value=MagicMock()),
        )
        monkeypatch.setattr(
            "factory.bootstrap.standalone.hub_standalone._build_hub_and_wire",
            AsyncMock(
                return_value=(
                    MagicMock(inbound_bus=AsyncMock(start=AsyncMock())),
                    [],
                    [],
                    MagicMock(),
                    MagicMock(),
                )
            ),
        )
        monkeypatch.setattr(
            "factory.bootstrap.standalone.hub_standalone.start_mint_failure_subscriber",
            AsyncMock(return_value=MagicMock()),
        )
        monkeypatch.setattr(
            "factory.infrastructure.outbound_audio.stream_setup.ensure_stream",
            AsyncMock(),
        )
        monkeypatch.setattr(
            "factory.infrastructure.outbound_audio.stream_setup.ensure_kv",
            AsyncMock(return_value=MagicMock()),
        )
        async def _record_publish_watch(*_a, **_kw):
            call_order.append("publish_watch_channels")

        async def _record_publish_roster(*_a, **_kw):
            call_order.append("publish_bot_roster")

        monkeypatch.setattr(
            "factory.bootstrap.standalone.hub_standalone.publish_watch_channels",
            _record_publish_watch,
        )
        monkeypatch.setattr(
            "factory.bootstrap.standalone.hub_standalone.publish_bot_roster",
            _record_publish_roster,
        )

        async def _record_announce(*_a, **_kw):
            call_order.append("announce_hub_ready")
            raise SystemExit("test-sentinel")

        monkeypatch.setattr(
            "factory.bootstrap.standalone.hub_standalone.announce_hub_ready",
            _record_announce,
        )
        monkeypatch.setattr(
            "factory.bootstrap.standalone.hub_standalone.build_inbound_bus",
            lambda *_a, **_kw: (AsyncMock(), MagicMock()),
        )

        with pytest.raises(SystemExit, match="test-sentinel"):
            await _bootstrap_hub_standalone(
                {
                    "defaults": {"cwd": "/tmp"},
                    "admin": {"user_ids": ["test_admin"]},
                    "telegram": {"bots": []},
                    "discord": {"bots": []},
                    "auth": {"telegram_bots": [], "discord_bots": []},
                    "message_index": {},
                }
            )

        assert call_order.index("publish_watch_channels") < call_order.index(
            "publish_bot_roster"
        )
        assert call_order.index("publish_bot_roster") < call_order.index(
            "announce_hub_ready"
        )


class TestAdapterQuadletNoConfigDbMounts:
    def test_telegram_and_discord_quadlet_templates_have_no_config_db_mounts(self) -> None:
        root = Path(__file__).resolve().parents[2]
        for name in ("factory-telegram.container.tmpl", "factory-discord.container.tmpl"):
            text = (root / "deploy" / "quadlet" / name).read_text()
            assert "config.db" not in text
            assert "ROXABI_FACTORY_DIR" not in text