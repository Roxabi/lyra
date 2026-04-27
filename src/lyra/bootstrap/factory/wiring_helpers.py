"""Focused helpers extracted from _bootstrap_unified (V10 — ADR-059)."""

# pyright: reportAttributeAccessIssue=false, reportArgumentType=false

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from lyra.bootstrap.factory.agent_factory import _resolve_agents, _resolve_bot_agent_map
from lyra.bootstrap.factory.config import (
    MessageIndexConfig,
    _build_agent_overrides,
    _load_circuit_config,
    _load_cli_pool_config,
    _load_debouncer_config,
    _load_event_bus_config,
    _load_hub_config,
    _load_inbound_bus_config,
    _load_llm_config,
    _load_messages,
    _load_pairing_config,
    _load_pool_config,
)
from lyra.bootstrap.wiring.bootstrap_wiring import (
    _build_bot_auths,
    wire_discord_adapters,
    wire_telegram_adapters,
)
from lyra.config import load_multibot_config
from lyra.core.agent import Agent
from lyra.core.agent.agent_loader import agent_row_to_config
from lyra.core.cli.cli_pool import CliPool
from lyra.core.config import HubConfig
from lyra.core.hub import Hub
from lyra.core.hub.event_bus import PipelineEventBus
from lyra.core.messaging.message import InboundMessage
from lyra.infrastructure.audit import JetStreamAuditSink
from lyra.infrastructure.stores.pairing import PairingManager, set_pairing_manager
from lyra.nats.nats_bus import NatsBus
from lyra.nats.queue_groups import HUB_INBOUND

if TYPE_CHECKING:
    import nats
    from lyra.llm.drivers.cli_nats import CliNatsDriver
    from lyra.llm.drivers.nats_driver import NatsLlmDriver

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Return bundles (plain dataclasses — no framework imports)
# ---------------------------------------------------------------------------


@dataclass
class VoiceBundle:
    stt_service: object
    tts_service: object
    nats_llm_driver: "NatsLlmDriver | None"


@dataclass
class BotAuthBundle:
    tg_bot_auths: object
    dc_bot_auths: object
    bot_agent_map: dict
    agent_configs: dict[str, Agent]
    first_agent_config: Agent
    msg_manager: object
    circuit_registry: object
    admin_user_ids: list


@dataclass
class CliPoolBundle:
    cli_pool: CliPool
    cli_nats_driver: "CliNatsDriver | None"
    worker: object
    audit_sink: JetStreamAuditSink


# ---------------------------------------------------------------------------
# Phase helpers
# ---------------------------------------------------------------------------


async def _init_inbound_bus(
    nc: nats.aio.client.Client,
    raw_config: dict,
) -> NatsBus[InboundMessage]:
    """Build the inbound NatsBus from config."""
    inbound_bus_cfg = _load_inbound_bus_config(raw_config)
    return NatsBus(
        nc=nc,
        bot_id="hub",
        item_type=InboundMessage,
        staging_maxsize=inbound_bus_cfg.staging_maxsize,
        queue_group=HUB_INBOUND,
    )


async def _seed_auth(stores: object, raw_config: dict) -> None:
    """Seed auth store from config block."""
    auth_block: dict = raw_config.get("auth", {})
    for entry in auth_block.get("telegram_bots", []):
        synthetic = {"auth": {"telegram": entry}}
        await stores.auth.seed_from_config(synthetic, "telegram")
    for entry in auth_block.get("discord_bots", []):
        synthetic = {"auth": {"discord": entry}}
        await stores.auth.seed_from_config(synthetic, "discord")


async def _prune_message_index(stores: object, raw_config: dict) -> None:
    """Prune message index according to retention config."""
    mi_cfg = MessageIndexConfig(**raw_config.get("message_index", {}))
    pruned = await stores.message_index.cleanup_older_than(mi_cfg.retention_days)
    if pruned:
        log.info(
            "message_index: pruned %d entries older than %d days",
            pruned,
            mi_cfg.retention_days,
        )


async def _init_bot_auths_and_agents(
    stores: object,
    raw_config: dict,
) -> BotAuthBundle:
    """Resolve multibot config, build authenticators, load agent configs."""
    circuit_registry, admin_user_ids = _load_circuit_config(raw_config)

    try:
        tg_multi_cfg, dc_multi_cfg = load_multibot_config(raw_config)
    except ValueError as exc:
        raise SystemExit(str(exc))

    tg_bot_auths, dc_bot_auths = _build_bot_auths(
        raw_config,
        tg_multi_cfg,
        dc_multi_cfg,
        stores.auth,
        admin_user_ids,
        alias_store=stores.identity_alias,
    )
    log.info(
        "Authenticator: %d admin_user_id(s) configured",
        len(admin_user_ids),
    )

    if not tg_bot_auths and not dc_bot_auths:
        raise SystemExit(
            "No adapters configured — add at least one"
            " [[telegram.bots]] or [[discord.bots]] entry with"
            " a matching [[auth.telegram_bots]] or"
            " [[auth.discord_bots]] section to config.toml"
        )

    bot_agent_map = await _resolve_bot_agent_map(
        stores.agent, tg_multi_cfg.bots, dc_multi_cfg.bots
    )
    agent_names: set[str] = set(bot_agent_map.values())

    agent_configs: dict[str, Agent] = {}
    for n in sorted(agent_names):
        row = stores.agent.get(n)
        if row is not None:
            overrides = _build_agent_overrides(raw_config, n)
            agent_configs[n] = agent_row_to_config(
                row,
                instance_overrides=overrides.model_dump(),
            )
        else:
            log.error("Agent %r not found in DB — skipping", n)
    if not agent_configs:
        raise SystemExit(
            "No agent configs could be loaded — run"
            " 'lyra agent init' to seed the agents table"
        )

    first_agent_name = next(iter(sorted(agent_configs)))
    first_agent_config = agent_configs[first_agent_name]
    msg_manager = _load_messages(language=first_agent_config.i18n_language)

    return BotAuthBundle(
        tg_bot_auths=tg_bot_auths,
        dc_bot_auths=dc_bot_auths,
        bot_agent_map=bot_agent_map,
        agent_configs=agent_configs,
        first_agent_config=first_agent_config,
        msg_manager=msg_manager,
        circuit_registry=circuit_registry,
        admin_user_ids=admin_user_ids,
    )


async def _init_pairing(
    raw_config: dict,
    admin_user_ids: list,
    vault_dir: Path,
    stores: object,
) -> PairingManager | None:
    """Conditionally create and connect PairingManager."""
    pairing_config = _load_pairing_config(raw_config)
    if pairing_config.enabled and not admin_user_ids:
        log.warning(
            "Pairing enabled but [admin].user_ids is empty — "
            "/invite and /unpair require is_admin=True "
            "(granted to [admin].user_ids entries "
            "or users configured as OWNER in [[auth.*_bots]])"
        )
    pm: PairingManager | None = None
    if pairing_config.enabled:
        pm = PairingManager(
            config=pairing_config,
            db_path=vault_dir / "pairing.db",
            auth_store=stores.auth,
        )
        await pm.connect()
        set_pairing_manager(pm)
    return pm


async def _init_voice_services(
    nc: nats.aio.client.Client,
) -> VoiceBundle:
    """Start STT, TTS, and NATS LLM driver."""
    from lyra.bootstrap.factory.llm_overlay import init_nats_llm
    from lyra.bootstrap.factory.voice_overlay import init_nats_stt, init_nats_tts

    stt_service = init_nats_stt(nc)
    await stt_service.start()
    tts_service = init_nats_tts(nc)
    await tts_service.start()
    nats_llm_driver = await init_nats_llm(nc)
    return VoiceBundle(
        stt_service=stt_service,
        tts_service=tts_service,
        nats_llm_driver=nats_llm_driver,
    )


def _build_hub(  # noqa: PLR0913
    raw_config: dict,
    bundle: BotAuthBundle,
    voice: VoiceBundle,
    inbound_bus: NatsBus,
    pm: PairingManager | None,
    stores: object,
) -> Hub:
    """Construct HubConfig and Hub, wire stores and alias store."""
    cli_pool_cfg = _load_cli_pool_config(raw_config)
    hub_cfg = _load_hub_config(raw_config)
    pool_cfg = _load_pool_config(raw_config)
    debouncer_cfg = _load_debouncer_config(raw_config)
    event_bus_cfg = _load_event_bus_config(raw_config)
    inbound_bus_cfg = _load_inbound_bus_config(raw_config)
    event_bus = PipelineEventBus(maxsize=event_bus_cfg.queue_maxsize)

    hub_config = HubConfig(
        rate_limit=hub_cfg.rate_limit,
        rate_window=hub_cfg.rate_window,
        pool_ttl=hub_cfg.pool_ttl,
        debounce_ms=debouncer_cfg.default_debounce_ms,
        cancel_on_new_message=debouncer_cfg.cancel_on_new_message,
        turn_timeout=cli_pool_cfg.turn_timeout,
        safe_dispatch_timeout=pool_cfg.safe_dispatch_timeout,
        staging_maxsize=inbound_bus_cfg.staging_maxsize,
        platform_queue_maxsize=inbound_bus_cfg.platform_queue_maxsize,
        queue_depth_threshold=inbound_bus_cfg.queue_depth_threshold,
        max_merged_chars=debouncer_cfg.max_merged_chars,
    )

    hub = Hub(
        circuit_registry=bundle.circuit_registry,
        msg_manager=bundle.msg_manager,
        pairing_manager=pm,
        stt=voice.stt_service,
        tts=voice.tts_service,
        prefs_store=stores.prefs,
        event_bus=event_bus,
        inbound_bus=inbound_bus,
        config=hub_config,
    )
    hub.set_turn_store(stores.turn)
    hub.set_message_index(stores.message_index)

    # Wire alias_store (#472)
    stores.prefs.set_alias_store(stores.identity_alias)
    hub.set_alias_store(stores.identity_alias)

    return hub


async def _init_clipool(
    nc: nats.aio.client.Client,
    raw_config: dict,
    stores: object,
) -> CliPoolBundle:
    """Build CliNatsDriver, CliPool, CliPoolNatsWorker."""
    from lyra.adapters.clipool.clipool_worker import CliPoolNatsWorker
    from lyra.bootstrap.factory.hub_builder import build_cli_nats_driver

    cli_pool_cfg = _load_cli_pool_config(raw_config)
    audit_sink = JetStreamAuditSink()
    await audit_sink.provision(nc)

    cli_nats_driver = await build_cli_nats_driver(nc)
    cli_pool = CliPool(
        idle_ttl=cli_pool_cfg.idle_ttl,
        default_timeout=cli_pool_cfg.default_timeout,
        reaper_interval=cli_pool_cfg.reaper_interval,
        kill_timeout=cli_pool_cfg.kill_timeout,
        read_buffer_bytes=cli_pool_cfg.read_buffer_bytes,
        stdin_drain_timeout=cli_pool_cfg.stdin_drain_timeout,
        max_idle_retries=cli_pool_cfg.max_idle_retries,
        intermediate_timeout=cli_pool_cfg.intermediate_timeout,
        audit_sink=audit_sink,
    )
    await cli_pool.start()
    cli_pool.set_turn_store(stores.turn)
    worker = CliPoolNatsWorker(cli_pool, timeout=cli_pool_cfg.default_timeout)

    return CliPoolBundle(
        cli_pool=cli_pool,
        cli_nats_driver=cli_nats_driver,
        worker=worker,
        audit_sink=audit_sink,
    )


def _register_agents(  # noqa: PLR0913
    hub: Hub,
    bundle: BotAuthBundle,
    voice: VoiceBundle,
    clipool: CliPoolBundle,
    raw_config: dict,
    stores: object,
) -> None:
    """Resolve agents, register them on hub, wire alias stores into memory managers."""
    llm_cfg = _load_llm_config(raw_config)
    all_agents = _resolve_agents(
        bundle.agent_configs,
        None,
        bundle.circuit_registry,
        bundle.msg_manager,
        voice.stt_service,
        voice.tts_service,
        agent_store=stores.agent,
        llm_cfg=llm_cfg,
        nats_llm_driver=voice.nats_llm_driver,
        cli_nats_driver=clipool.cli_nats_driver,
    )
    for ag in all_agents.values():
        hub.register_agent(ag)

    # Wire alias_store into MemoryManagers (#472)
    for agent in hub.agent_registry.values():
        mem = getattr(agent, "_memory", None)
        if mem is not None and hasattr(mem, "set_alias_store"):
            mem.set_alias_store(stores.identity_alias)


async def _wire_adapters(
    hub: Hub,
    bundle: BotAuthBundle,
    nc: nats.aio.client.Client,
    stores: object,
    vault_dir: Path,
) -> tuple:
    """Wire Telegram and Discord adapters.

    Returns (tg_adapters, tg_dispatchers, dc_adapters, dc_dispatchers).
    """
    tg_adapters, tg_dispatchers = await wire_telegram_adapters(
        hub,
        bundle.tg_bot_auths,
        bundle.bot_agent_map,
        stores.cred,
        bundle.circuit_registry,
        bundle.msg_manager,
        nats_client=nc,
    )
    dc_adapters, dc_dispatchers = await wire_discord_adapters(
        hub,
        bundle.dc_bot_auths,
        bundle.bot_agent_map,
        stores.cred,
        bundle.circuit_registry,
        bundle.msg_manager,
        agent_store=stores.agent,
        vault_dir=str(vault_dir),
        nats_client=nc,
    )
    return tg_adapters, tg_dispatchers, dc_adapters, dc_dispatchers


async def _run_clipool_worker_task(
    worker: object,
    nc: nats.aio.client.Client,
) -> asyncio.Task:
    """Spawn the embedded clipool worker task."""
    return asyncio.create_task(worker.run_embedded(nc), name="clipool-worker")
