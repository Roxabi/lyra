"""Focused helpers extracted from _bootstrap_unified (V10 — ADR-059)."""

# pyright: reportAttributeAccessIssue=false, reportArgumentType=false

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from lyra.bootstrap.factory.agent_factory import _resolve_agents
from lyra.bootstrap.factory.config import (
    MessageIndexConfig,
    _load_inbound_bus_config,
    _load_llm_config,
    _load_pairing_config,
    build_adapter_config_bundle,
)
from lyra.bootstrap.types import (
    DiscordAdapterEntry,
    RegisterAgentsDeps,
    VoiceBundle,
    WireAdaptersDeps,
    WiredAdapters,
)
from lyra.bootstrap.wiring.bootstrap_wiring import (
    wire_discord_adapters,
    wire_telegram_adapters,
)
from lyra.core.messaging.message import InboundMessage
from lyra.infrastructure.stores.pairing import PairingManager, set_pairing_manager
from lyra.nats.nats_bus import NatsBus
from lyra.nats.queue_groups import HUB_INBOUND

if TYPE_CHECKING:
    import nats

log = logging.getLogger(__name__)


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


async def _seed_auth(stores: object) -> None:
    """Thin shim: delegate to canonical seed_grants_from_bots."""
    from lyra.bootstrap.auth_seeding import seed_grants_from_bots

    await seed_grants_from_bots(stores.auth, stores.bot)


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


async def _init_pairing(
    raw_config: dict,
    admin_user_ids: frozenset[str],
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
    nats_llm_client = await init_nats_llm(nc)
    return VoiceBundle(
        stt_service=stt_service,
        tts_service=tts_service,
        nats_llm_client=nats_llm_client,
    )


def _register_agents(deps: RegisterAgentsDeps) -> None:
    """Resolve agents, register them on hub, wire alias stores into memory managers."""
    from lyra.bootstrap.factory.agent_factory import ResolveAgentsDeps

    llm_cfg = _load_llm_config(deps.raw_config)
    all_agents = _resolve_agents(
        ResolveAgentsDeps(
            agent_configs=deps.bundle.agent_configs,
            cli_pool=None,
            circuit_registry=deps.bundle.circuit_registry,
            msg_manager=deps.bundle.msg_manager,
            stt_service=deps.voice.stt_service,
            tts_service=deps.voice.tts_service,
            agent_store=deps.stores.agent,
            llm_cfg=llm_cfg,
            nats_llm_client=deps.voice.nats_llm_client,
            cli_nats_driver=deps.clipool.cli_nats_driver,
        )
    )
    for ag in all_agents.values():
        deps.hub.register_agent(ag)

    # Wire alias_store into MemoryManagers (#472)
    for agent in deps.hub.agent_registry.values():
        mem = getattr(agent, "_memory", None)
        if mem is not None and hasattr(mem, "set_alias_store"):
            mem.set_alias_store(deps.stores.identity_alias)


async def _wire_adapters(deps: WireAdaptersDeps) -> WiredAdapters:
    """Wire Telegram and Discord adapters."""
    from lyra.bootstrap.wiring.bootstrap_wiring import (
        DiscordWiringDeps,
        TelegramWiringDeps,
    )

    config_bundle = build_adapter_config_bundle(deps.raw_config)
    tg_adapters, tg_dispatchers = await wire_telegram_adapters(
        TelegramWiringDeps(
            hub=deps.hub,
            tg_bot_auths=deps.bundle.tg_bot_auths,
            bot_agent_map=deps.bundle.bot_agent_map,
            circuit_registry=deps.bundle.circuit_registry,
            msg_manager=deps.bundle.msg_manager,
            nats_client=deps.nc,
            tool_display_config=config_bundle.tool_display,
            blob_store=deps.blob_store,
        )
    )
    dc_adapters, dc_dispatchers, dc_thread_store = await wire_discord_adapters(
        DiscordWiringDeps(
            hub=deps.hub,
            dc_bot_auths=deps.bundle.dc_bot_auths,
            bot_agent_map=deps.bundle.bot_agent_map,
            circuit_registry=deps.bundle.circuit_registry,
            msg_manager=deps.bundle.msg_manager,
            agent_store=deps.stores.agent,
            vault_dir=str(deps.vault_dir),
            nats_client=deps.nc,
            tool_display_config=config_bundle.tool_display,
            blob_store=deps.blob_store,
        )
    )
    return WiredAdapters(
        tg_adapters=tg_adapters,
        tg_dispatchers=tg_dispatchers,
        dc_adapters=[DiscordAdapterEntry(*t) for t in dc_adapters],
        dc_dispatchers=dc_dispatchers,
        dc_thread_store=dc_thread_store,
    )


async def _run_clipool_worker_task(
    worker: object,
    nc: nats.aio.client.Client,
) -> asyncio.Task:
    """Spawn the embedded clipool worker task."""
    return asyncio.create_task(worker.run_embedded(nc), name="clipool-worker")
