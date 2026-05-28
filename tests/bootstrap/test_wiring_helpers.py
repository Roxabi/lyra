"""Tests for wiring_helpers — phase, construction, and wiring slices (T3/T4/T5)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import lyra.bootstrap.factory.agent_factory as agent_factory_mod
import lyra.bootstrap.factory.hub_builder as hub_builder_mod
import lyra.bootstrap.factory.wiring_helpers as wiring_helpers_mod
from lyra.bootstrap.factory.agent_factory import _init_bot_auths_and_agents
from lyra.bootstrap.factory.hub_builder import _build_hub, _init_clipool
from lyra.bootstrap.factory.wiring_helpers import (
    _init_inbound_bus,
    _init_pairing,
    _init_voice_services,
    _prune_message_index,
    _register_agents,
    _run_clipool_worker_task,
    _seed_auth,
    _wire_adapters,
)
from lyra.bootstrap.types import (
    BotAuthBundle,
    BuildHubDeps,
    CliPoolBundle,
    RegisterAgentsDeps,
    VoiceBundle,
    WireAdaptersDeps,
    WiredAdapters,
)
from lyra.core.agent import Agent
from lyra.core.agent.agent_config import ModelConfig
from lyra.core.circuit_breaker import CircuitBreaker, CircuitRegistry
from lyra.core.hub import Hub
from lyra.core.messaging.tool_display_config import ToolDisplayConfig
from lyra.nats.queue_groups import HUB_INBOUND

# ---------------------------------------------------------------------------
# T3 — Simple phase helpers
# ---------------------------------------------------------------------------


class TestInitInboundBus:
    @pytest.mark.asyncio
    async def test_init_inbound_bus_returns_nats_bus_with_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        captured_kwargs: dict[str, Any] = {}
        fake_bus = MagicMock()

        def capture_nats_bus(**kwargs: Any) -> MagicMock:
            captured_kwargs.update(kwargs)
            return fake_bus

        monkeypatch.setattr(
            wiring_helpers_mod,
            "_load_inbound_bus_config",
            lambda raw: MagicMock(staging_maxsize=123),
        )
        monkeypatch.setattr(wiring_helpers_mod, "NatsBus", capture_nats_bus)
        fake_nc = MagicMock()

        # Act
        result = await _init_inbound_bus(fake_nc, {})

        # Assert
        assert result is fake_bus
        assert captured_kwargs["queue_group"] == HUB_INBOUND
        assert captured_kwargs["bot_id"] == "hub"


class TestSeedAuth:
    @pytest.mark.asyncio
    async def test_seed_auth_delegates_to_seed_grants_from_bots(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        seed_calls: list[tuple] = []

        async def fake_seed(auth_store, bot_store):
            seed_calls.append((auth_store, bot_store))

        monkeypatch.setattr(
            "lyra.bootstrap.auth_seeding.seed_grants_from_bots",
            fake_seed,
        )

        stores = MagicMock()
        stores.auth = MagicMock()
        stores.bot = MagicMock()

        # Act
        await _seed_auth(stores)

        # Assert — delegates to canonical seed_grants_from_bots with auth+bot stores
        assert len(seed_calls) == 1
        passed_auth, passed_bot = seed_calls[0]
        assert passed_auth is stores.auth
        assert passed_bot is stores.bot


class TestPruneMessageIndex:
    @pytest.mark.asyncio
    async def test_prune_message_index_calls_cleanup_older_than_and_logs_when_positive(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Arrange
        stores = MagicMock()
        stores.message_index.cleanup_older_than = AsyncMock(return_value=5)
        raw_config = {"message_index": {"retention_days": 30}}

        # Act
        with caplog.at_level(logging.INFO):
            await _prune_message_index(stores, raw_config)

        # Assert
        stores.message_index.cleanup_older_than.assert_awaited_once_with(30)
        assert "pruned 5 entries" in caplog.text

    @pytest.mark.asyncio
    async def test_prune_message_index_calls_cleanup_older_than_and_skips_log_when_zero(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Arrange
        stores = MagicMock()
        stores.message_index.cleanup_older_than = AsyncMock(return_value=0)
        raw_config = {"message_index": {"retention_days": 7}}

        # Act
        with caplog.at_level(logging.INFO):
            await _prune_message_index(stores, raw_config)

        # Assert
        stores.message_index.cleanup_older_than.assert_awaited_once_with(7)
        assert "pruned" not in caplog.text


class TestInitPairing:
    @pytest.mark.asyncio
    async def test_init_pairing_returns_none_when_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        monkeypatch.setattr(
            wiring_helpers_mod,
            "_load_pairing_config",
            lambda raw: MagicMock(enabled=False),
        )

        # Act
        result = await _init_pairing({}, frozenset(), Path("/tmp"), MagicMock())

        # Assert
        assert result is None

    @pytest.mark.asyncio
    async def test_init_pairing_returns_manager_and_connects_when_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        mock_pm = MagicMock()
        mock_pm.connect = AsyncMock()
        monkeypatch.setattr(
            wiring_helpers_mod,
            "_load_pairing_config",
            lambda raw: MagicMock(enabled=True),
        )
        mock_set_pm = MagicMock()
        monkeypatch.setattr(wiring_helpers_mod, "PairingManager", lambda **kw: mock_pm)
        monkeypatch.setattr(wiring_helpers_mod, "set_pairing_manager", mock_set_pm)

        # Act
        result = await _init_pairing(
            {}, frozenset(["tg:user:123"]), Path("/tmp"), MagicMock()
        )

        # Assert
        assert result is mock_pm
        mock_pm.connect.assert_awaited_once()
        mock_set_pm.assert_called_once_with(mock_pm)

    @pytest.mark.asyncio
    async def test_init_pairing_warns_when_enabled_without_admins(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        mock_pm = MagicMock()
        mock_pm.connect = AsyncMock()
        monkeypatch.setattr(
            wiring_helpers_mod,
            "_load_pairing_config",
            lambda raw: MagicMock(enabled=True, admin_user_ids=[]),
        )
        monkeypatch.setattr(wiring_helpers_mod, "PairingManager", lambda **kw: mock_pm)
        monkeypatch.setattr(wiring_helpers_mod, "set_pairing_manager", MagicMock())

        with caplog.at_level(logging.WARNING):
            result = await _init_pairing({}, frozenset(), Path("/tmp"), MagicMock())

        assert result is mock_pm
        assert "[admin].user_ids is empty" in caplog.text


class TestInitVoiceServices:
    @pytest.mark.asyncio
    async def test_init_voice_services_returns_voice_bundle_with_all_services_started(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        fake_stt = MagicMock()
        fake_stt.start = AsyncMock()
        fake_tts = MagicMock()
        fake_tts.start = AsyncMock()
        fake_llm = MagicMock()

        monkeypatch.setattr(
            "lyra.bootstrap.factory.voice_overlay.init_nats_stt",
            lambda nc: fake_stt,
        )
        monkeypatch.setattr(
            "lyra.bootstrap.factory.voice_overlay.init_nats_tts",
            lambda nc: fake_tts,
        )
        monkeypatch.setattr(
            "lyra.bootstrap.factory.llm_overlay.init_nats_llm",
            AsyncMock(return_value=fake_llm),
        )
        fake_nc = MagicMock()

        # Act
        result = await _init_voice_services(fake_nc)

        # Assert
        assert isinstance(result, VoiceBundle)
        assert result.stt_service is fake_stt
        assert result.tts_service is fake_tts
        assert result.nats_llm_client is fake_llm
        fake_stt.start.assert_awaited_once()
        fake_tts.start.assert_awaited_once()


class TestRunClipoolWorkerTask:
    @pytest.mark.asyncio
    async def test_run_clipool_worker_task_returns_asyncio_task_named_clipool_worker(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        fake_task = MagicMock()
        fake_task.name = "clipool-worker"
        monkeypatch.setattr(
            wiring_helpers_mod.asyncio,
            "create_task",
            lambda coro, name=None: fake_task,
        )
        worker = MagicMock()
        fake_nc = MagicMock()

        # Act
        result = await _run_clipool_worker_task(worker, fake_nc)

        # Assert
        assert result is fake_task


# ---------------------------------------------------------------------------
# T4 — Construction helpers
# ---------------------------------------------------------------------------


class TestInitBotAuthsAndAgents:
    @pytest.mark.asyncio
    async def test_init_bot_auths_and_agents_builds_bundle_with_correct_fields(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange — patch intra-factory collaborators at boundary
        circuit_reg = CircuitRegistry()
        circuit_reg.register(CircuitBreaker(name="claude-cli"))
        monkeypatch.setattr(
            agent_factory_mod,
            "_load_circuit_config",
            lambda raw: (circuit_reg, frozenset(["tg:user:123"])),
        )

        tg_cfg = MagicMock()
        tg_cfg.bots = [MagicMock(bot_id="main")]
        dc_cfg = MagicMock()
        dc_cfg.bots = []
        # Roster now comes from multibot_config_from_store(stores.bot), not TOML
        monkeypatch.setattr(
            agent_factory_mod,
            "multibot_config_from_store",
            lambda bot_store: (tg_cfg, dc_cfg),
        )

        mock_tg_auth = MagicMock()
        monkeypatch.setattr(
            agent_factory_mod,
            "_build_bot_auths",
            lambda *a, **kw: ([(tg_cfg.bots[0], mock_tg_auth)], []),
        )

        monkeypatch.setattr(
            agent_factory_mod,
            "_resolve_bot_agent_map",
            AsyncMock(return_value={("telegram", "main"): "lyra_default"}),
        )

        monkeypatch.setattr(
            agent_factory_mod,
            "_build_agent_overrides",
            lambda raw, name: MagicMock(model_dump=lambda: {}),
        )

        fake_agent = Agent(
            name="lyra_default",
            system_prompt="test",
            memory_namespace="test",
            llm_config=ModelConfig(backend="claude-cli"),
        )
        monkeypatch.setattr(
            agent_factory_mod,
            "agent_row_to_config",
            lambda row, **kw: fake_agent,
        )

        mock_msg_mgr = MagicMock()

        def _load_messages(language: str) -> MagicMock:
            return mock_msg_mgr

        monkeypatch.setattr(agent_factory_mod, "_load_messages", _load_messages)

        stores = MagicMock()
        stores.agent.get = MagicMock(return_value=MagicMock(name="lyra_default"))

        # Act
        result = await _init_bot_auths_and_agents(stores, {})

        # Assert
        assert isinstance(result, BotAuthBundle)
        assert result.tg_bot_auths == [(tg_cfg.bots[0], mock_tg_auth)]
        assert result.dc_bot_auths == []
        assert result.bot_agent_map == {("telegram", "main"): "lyra_default"}
        assert result.agent_configs == {"lyra_default": fake_agent}
        assert result.first_agent_config is fake_agent
        assert result.msg_manager is mock_msg_mgr
        assert result.circuit_registry is circuit_reg
        assert result.admin_user_ids == frozenset(["tg:user:123"])

    @pytest.mark.asyncio
    async def test_init_bot_auths_exits_when_no_bots_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SC#3 — empty BotStore roster raises SystemExit with 'lyra bot init' hint.

        Negative gate: deleting the empty-roster guard in _init_bot_auths_and_agents
        means no SystemExit is raised and the test fails.
        """
        monkeypatch.setattr(
            agent_factory_mod,
            "_load_circuit_config",
            lambda raw: (MagicMock(), frozenset()),
        )
        # Roster sourced from store — return empty configs (no bots in BotStore)
        monkeypatch.setattr(
            agent_factory_mod,
            "multibot_config_from_store",
            lambda bot_store: (MagicMock(bots=[]), MagicMock(bots=[])),
        )
        monkeypatch.setattr(
            agent_factory_mod,
            "_build_bot_auths",
            lambda *a, **kw: ([], []),
        )

        with pytest.raises(ValueError) as exc_info:
            await _init_bot_auths_and_agents(MagicMock(), {})

        assert "lyra bot init" in str(exc_info.value), (
            f"Expected 'lyra bot init' in error message, got: {exc_info.value!r}"
        )

    @pytest.mark.asyncio
    async def test_init_bot_auths_exits_when_no_agent_configs_loaded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            agent_factory_mod,
            "_load_circuit_config",
            lambda raw: (MagicMock(), frozenset()),
        )
        # Roster sourced from store — one telegram bot present
        monkeypatch.setattr(
            agent_factory_mod,
            "multibot_config_from_store",
            lambda bot_store: (MagicMock(bots=[MagicMock()]), MagicMock(bots=[])),
        )
        monkeypatch.setattr(
            agent_factory_mod,
            "_build_bot_auths",
            lambda *a, **kw: ([(MagicMock(), MagicMock())], []),
        )
        monkeypatch.setattr(
            agent_factory_mod,
            "_resolve_bot_agent_map",
            AsyncMock(return_value={("telegram", "main"): "missing"}),
        )
        stores = MagicMock()
        stores.agent.get = MagicMock(return_value=None)

        with pytest.raises(ValueError):
            await _init_bot_auths_and_agents(stores, {})

    @pytest.mark.asyncio
    async def test_init_bot_auths_exits_on_bad_multibot_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Old behavior: ValueError from load_multibot_config (TOML parsing) was wrapped
        into SystemExit. That wrapping is GONE — the store builder does not parse TOML.

        Repurposed: assert that BotStore.get_all() raising propagates unhandled through
        _init_bot_auths_and_agents. This tests the boundary between the store and the
        factory: a broken store is an infrastructure failure, not a configuration error,
        so we expect the exception to surface (not be silently swallowed).

        Choice rationale: option (a) — BotStore raising on get_all() propagates — is
        preferred over merging with the empty-store test (#3) because it exercises a
        distinct failure mode (store error vs. empty store) and keeps the negative-test
        guard distinct and non-redundant.
        """
        monkeypatch.setattr(
            agent_factory_mod,
            "_load_circuit_config",
            lambda raw: (MagicMock(), frozenset()),
        )
        # Simulate a broken BotStore that raises on get_all()
        monkeypatch.setattr(
            agent_factory_mod,
            "multibot_config_from_store",
            MagicMock(side_effect=RuntimeError("store unavailable")),
        )

        with pytest.raises(RuntimeError, match="store unavailable"):
            await _init_bot_auths_and_agents(MagicMock(), {})


class TestBuildHub:
    def test_build_hub_constructs_hub_with_config_and_wires_stores(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange — patch all config loaders
        monkeypatch.setattr(
            hub_builder_mod,
            "_load_cli_pool_config",
            lambda raw: MagicMock(turn_timeout=30.0),
        )
        monkeypatch.setattr(
            hub_builder_mod,
            "_load_hub_config",
            lambda raw: MagicMock(rate_limit=10, rate_window=30, pool_ttl=3600.0),
        )
        monkeypatch.setattr(
            hub_builder_mod,
            "_load_pool_config",
            lambda raw: MagicMock(safe_dispatch_timeout=5.0),
        )
        monkeypatch.setattr(
            hub_builder_mod,
            "_load_debouncer_config",
            lambda raw: MagicMock(
                default_debounce_ms=200,
                cancel_on_new_message=True,
                max_merged_chars=2048,
            ),
        )
        monkeypatch.setattr(
            hub_builder_mod,
            "_load_event_bus_config",
            lambda raw: MagicMock(queue_maxsize=500),
        )
        monkeypatch.setattr(
            hub_builder_mod,
            "_load_inbound_bus_config",
            lambda raw: MagicMock(
                staging_maxsize=100, platform_queue_maxsize=50, queue_depth_threshold=25
            ),
        )

        mock_event_bus = MagicMock()

        def _event_bus(maxsize: int) -> MagicMock:
            return mock_event_bus

        monkeypatch.setattr(hub_builder_mod, "PipelineEventBus", _event_bus)
        mock_hub = MagicMock()
        monkeypatch.setattr(hub_builder_mod, "Hub", lambda **kw: mock_hub)

        bundle = BotAuthBundle(
            tg_bot_auths=[],
            dc_bot_auths=[],
            bot_agent_map={},
            agent_configs={},
            first_agent_config=MagicMock(),
            msg_manager=MagicMock(),
            circuit_registry=MagicMock(),
            admin_user_ids=frozenset(),
        )
        voice = VoiceBundle(
            stt_service=MagicMock(),
            tts_service=MagicMock(),
            nats_llm_client=None,
        )
        inbound_bus = MagicMock()
        pm = None
        stores = MagicMock()

        # Act
        result = _build_hub(
            BuildHubDeps(
                raw_config={},
                bundle=bundle,
                voice=voice,
                inbound_bus=inbound_bus,
                pm=pm,
                stores=stores,
            )
        )

        # Assert
        assert result is mock_hub
        mock_hub.set_turn_store.assert_called_once_with(stores.turn)
        mock_hub.set_message_index.assert_called_once_with(stores.message_index)
        stores.prefs.set_alias_store.assert_called_once_with(stores.identity_alias)
        mock_hub.set_alias_store.assert_called_once_with(stores.identity_alias)


class TestInitClipool:
    @pytest.mark.asyncio
    async def test_init_clipool_constructs_bundle_provisions_and_starts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        monkeypatch.setattr(
            hub_builder_mod,
            "_load_cli_pool_config",
            lambda raw: MagicMock(
                idle_ttl=1200,
                default_timeout=1200,
                reaper_interval=60,
                kill_timeout=5.0,
                read_buffer_bytes=1024 * 1024,
                stdin_drain_timeout=10.0,
                max_idle_retries=3,
                intermediate_timeout=5.0,
            ),
        )

        mock_audit = MagicMock()
        mock_audit.provision = AsyncMock()

        def _audit_sink() -> MagicMock:
            return mock_audit

        monkeypatch.setattr(hub_builder_mod, "JetStreamAuditSink", _audit_sink)

        mock_cli_pool = MagicMock()
        mock_cli_pool.start = AsyncMock()
        mock_cli_pool.set_turn_store = MagicMock()
        monkeypatch.setattr(hub_builder_mod, "CliPool", lambda **kw: mock_cli_pool)

        mock_llm_client = MagicMock()
        monkeypatch.setattr(
            "lyra.bootstrap.factory.hub_builder.build_llm_client",
            AsyncMock(return_value=mock_llm_client),
        )

        mock_worker = MagicMock()
        monkeypatch.setattr(
            "lyra.adapters.clipool.clipool_worker.CliPoolNatsWorker",
            lambda *a, **kw: mock_worker,
        )

        stores = MagicMock()
        fake_nc = MagicMock()

        # Act
        result = await _init_clipool(fake_nc, {}, stores)

        # Assert
        assert isinstance(result, CliPoolBundle)
        assert result.cli_pool is mock_cli_pool
        assert result.cli_nats_driver is mock_llm_client
        assert result.worker is mock_worker
        assert result.audit_sink is mock_audit
        mock_audit.provision.assert_awaited_once_with(fake_nc)
        mock_cli_pool.start.assert_awaited_once()
        mock_cli_pool.set_turn_store.assert_called_once_with(stores.turn)


# ---------------------------------------------------------------------------
# T5 — Wiring helpers
# ---------------------------------------------------------------------------


class TestRegisterAgents:
    def test_register_agents_calls_resolve_agents_and_registers_each_and_wires_alias(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        mock_alpha = MagicMock()
        mock_alpha.name = "alpha"
        mock_beta = MagicMock()
        mock_beta.name = "beta"
        mock_resolve = MagicMock(return_value={"alpha": mock_alpha, "beta": mock_beta})
        monkeypatch.setattr(wiring_helpers_mod, "_resolve_agents", mock_resolve)

        mock_llm_cfg = MagicMock()

        def _llm_cfg(raw: dict) -> MagicMock:
            return mock_llm_cfg

        monkeypatch.setattr(wiring_helpers_mod, "_load_llm_config", _llm_cfg)

        hub = Hub(circuit_registry=CircuitRegistry())
        hub.register_agent = MagicMock()

        bundle = BotAuthBundle(
            tg_bot_auths=[],
            dc_bot_auths=[],
            bot_agent_map={},
            agent_configs={"alpha": MagicMock(), "beta": MagicMock()},
            first_agent_config=MagicMock(),
            msg_manager=MagicMock(),
            circuit_registry=CircuitRegistry(),
            admin_user_ids=frozenset(),
        )
        voice = VoiceBundle(stt_service=None, tts_service=None, nats_llm_client=None)
        clipool = CliPoolBundle(
            cli_pool=MagicMock(),
            cli_nats_driver=None,
            worker=MagicMock(),
            audit_sink=MagicMock(),
        )
        stores = MagicMock()

        # Pre-seed an agent with _memory so alias wiring is exercisable
        mem_agent = MagicMock()
        mem_agent._memory = MagicMock()
        mem_agent._memory.set_alias_store = MagicMock()
        hub.agent_registry = {"mem_agent": mem_agent}

        from lyra.bootstrap.factory.agent_factory import ResolveAgentsDeps

        # Act
        _register_agents(
            RegisterAgentsDeps(
                hub=hub,
                bundle=bundle,
                voice=voice,
                clipool=clipool,
                raw_config={},
                stores=stores,
            )
        )

        # Assert — resolve called with correct args
        mock_resolve.assert_called_once_with(
            ResolveAgentsDeps(
                agent_configs=bundle.agent_configs,
                cli_pool=None,
                circuit_registry=bundle.circuit_registry,  # type: ignore[reportArgumentType]
                msg_manager=bundle.msg_manager,  # type: ignore[reportArgumentType]
                stt_service=voice.stt_service,  # type: ignore[reportArgumentType]
                tts_service=voice.tts_service,  # type: ignore[reportArgumentType]
                agent_store=stores.agent,
                llm_cfg=mock_llm_cfg,
                nats_llm_client=voice.nats_llm_client,
                cli_nats_driver=clipool.cli_nats_driver,
            )
        )

        # Assert — every agent registered
        hub.register_agent.assert_any_call(mock_alpha)
        hub.register_agent.assert_any_call(mock_beta)
        assert hub.register_agent.call_count == 2

        # Assert — alias store wired into memory managers
        mem_agent._memory.set_alias_store.assert_called_once_with(stores.identity_alias)


class TestWireAdapters:
    @pytest.mark.asyncio
    async def test_wire_adapters_calls_wire_telegram_and_wire_discord_with_correct_args(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        mock_tg_adapter = MagicMock()
        mock_tg_dispatcher = MagicMock()
        mock_dc_adapter_tuple = (MagicMock(), MagicMock(), "token")
        mock_dc_dispatcher = MagicMock()
        mock_dc_thread_store = MagicMock()

        mock_wire_tg = AsyncMock(return_value=([mock_tg_adapter], [mock_tg_dispatcher]))
        monkeypatch.setattr(wiring_helpers_mod, "wire_telegram_adapters", mock_wire_tg)
        mock_wire_dc = AsyncMock(
            return_value=(
                [mock_dc_adapter_tuple],
                [mock_dc_dispatcher],
                mock_dc_thread_store,
            )
        )
        monkeypatch.setattr(wiring_helpers_mod, "wire_discord_adapters", mock_wire_dc)

        hub = MagicMock()
        bundle = BotAuthBundle(
            tg_bot_auths=[(MagicMock(), MagicMock())],
            dc_bot_auths=[(MagicMock(), MagicMock())],
            bot_agent_map={("telegram", "main"): "lyra_default"},
            agent_configs={},
            first_agent_config=MagicMock(),
            msg_manager=MagicMock(),
            circuit_registry=MagicMock(),
            admin_user_ids=frozenset(),
        )
        stores = MagicMock()
        fake_nc = MagicMock()
        vault_dir = Path("/tmp/fake_vault")

        from lyra.bootstrap.wiring.bootstrap_wiring import (
            DiscordWiringDeps,
            TelegramWiringDeps,
        )

        # Act
        result = await _wire_adapters(
            WireAdaptersDeps(
                hub=hub,
                bundle=bundle,
                nc=fake_nc,
                stores=stores,
                vault_dir=vault_dir,
                raw_config={},
            )
        )

        # Assert — _wire_adapters calls _load_tool_display_config({}) which returns
        # ToolDisplayConfig() defaults; the loader call is part of the contract and
        # value-equality catches the "loader silently removed" regression that ANY
        # would have masked.
        expected_tdc = ToolDisplayConfig()
        mock_wire_tg.assert_awaited_once_with(
            TelegramWiringDeps(  # type: ignore[reportArgumentType]
                hub=hub,
                tg_bot_auths=bundle.tg_bot_auths,  # type: ignore[reportArgumentType]
                bot_agent_map=bundle.bot_agent_map,
                circuit_registry=bundle.circuit_registry,  # type: ignore[reportArgumentType]
                msg_manager=bundle.msg_manager,  # type: ignore[reportArgumentType]
                nats_client=fake_nc,
                tool_display_config=expected_tdc,
            )
        )
        mock_wire_dc.assert_awaited_once_with(
            DiscordWiringDeps(  # type: ignore[reportArgumentType]
                hub=hub,
                dc_bot_auths=bundle.dc_bot_auths,  # type: ignore[reportArgumentType]
                bot_agent_map=bundle.bot_agent_map,
                circuit_registry=bundle.circuit_registry,  # type: ignore[reportArgumentType]
                msg_manager=bundle.msg_manager,  # type: ignore[reportArgumentType]
                agent_store=stores.agent,
                vault_dir=str(vault_dir),
                nats_client=fake_nc,
                tool_display_config=expected_tdc,
            )
        )

        assert isinstance(result, WiredAdapters)
        assert result.tg_adapters == [mock_tg_adapter]
        assert result.tg_dispatchers == [mock_tg_dispatcher]
        assert result.dc_adapters == [
            wiring_helpers_mod.DiscordAdapterEntry(*mock_dc_adapter_tuple)
        ]
        assert result.dc_dispatchers == [mock_dc_dispatcher]
        assert result.dc_thread_store is mock_dc_thread_store
