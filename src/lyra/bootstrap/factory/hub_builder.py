"""Hub construction helpers for standalone Hub bootstrap.

Extracted from hub_standalone.py for size compliance (#760).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from nats.aio.client import Client as NATS

from lyra.bootstrap.factory.agent_factory import ResolveAgentsDeps, _resolve_agents
from lyra.bootstrap.factory.config import (
    InboundBusConfig,
    _load_cli_pool_config,
    _load_debouncer_config,
    _load_event_bus_config,
    _load_hub_config,
    _load_inbound_bus_config,
    _load_llm_config,
    _load_pool_config,
)
from lyra.bootstrap.factory.llm_overlay import init_nats_llm
from lyra.bootstrap.factory.voice_overlay import (
    init_blobstore,
    init_nats_stt,
    init_nats_tts,
)
from lyra.bootstrap.wiring.nats_wiring import (
    NatsDcWiringDeps,
    NatsTgWiringDeps,
    wire_nats_discord_proxies,
    wire_nats_telegram_proxies,
)
from lyra.core.agent import Agent
from lyra.core.circuit_breaker import CircuitRegistry
from lyra.core.cli.cli_pool import CliPool
from lyra.core.config import HubConfig
from lyra.core.hub import Hub
from lyra.core.hub.event_bus import PipelineEventBus
from lyra.core.messaging.message import InboundMessage
from lyra.core.ports.stt import STTProtocol
from lyra.core.ports.tts import TtsProtocol
from lyra.infrastructure.audit import JetStreamAuditSink
from lyra.infrastructure.resume_publisher_adapter import TurnPublisherAdapter
from lyra.infrastructure.stores.agent_store import AgentStore
from lyra.nats.nats_bus import NatsBus
from lyra.nats.queue_groups import HUB_INBOUND
from lyra.transport.turn_publisher import TurnPublisher
from lyra.transport.typing_publisher import TypingPublisher

if TYPE_CHECKING:
    from lyra.bootstrap.bootstrap_stores import StoreBundle
    from lyra.bootstrap.wiring.bootstrap_wiring import Authenticator
    from lyra.config import DiscordBotConfig, TelegramBotConfig
    from lyra.core.hub import OutboundDispatcher
    from lyra.core.messaging.messages import MessageManager
    from lyra.core.ports.audit_sink import AuditSink
    from lyra.infrastructure.stores.pairing import PairingManager
    from lyra.llm.llm_client import LlmClient
    from lyra.nats.nats_channel_proxy import NatsChannelProxy

from lyra.bootstrap.types import BotAuthBundle, BuildHubDeps, CliPoolBundle, VoiceBundle

log = logging.getLogger(__name__)


async def build_llm_client(
    nc: NATS,
    *,
    timeout: float = 120.0,
) -> "LlmClient":
    """Build and start an LlmClient connected to the clipool worker via NATS."""
    from lyra.llm.cli_pool_codec import CliPoolCodec
    from lyra.llm.llm_client import LlmClient
    from lyra.nats.worker_registry import WorkerRegistry
    from lyra.transport.nats_request_response import NatsTransport
    from lyra.transport.worker_pool_client import WorkerPoolClient
    from roxabi_contracts._nats_utils import validate_worker_id

    transport = NatsTransport(nc)
    pool = WorkerPoolClient(
        transport,
        registry=WorkerRegistry(),
        hb_subject="lyra.clipool.heartbeat",
        validate_worker_id=validate_worker_id,
        name="clipool",
    )
    await pool.start(nc)
    return LlmClient(
        pool,
        CliPoolCodec(),
        timeout=timeout,
        request_subject="lyra.clipool.cmd",
    )


def build_inbound_bus(
    nc: NATS, raw_config: dict
) -> tuple[NatsBus[InboundMessage], InboundBusConfig]:
    """Create NatsBus for inbound messages and return (bus, inbound_bus_cfg)."""
    inbound_bus_cfg = _load_inbound_bus_config(raw_config)
    inbound_bus: NatsBus[InboundMessage] = NatsBus(
        nc=nc,
        bot_id="hub",
        item_type=InboundMessage,
        staging_maxsize=inbound_bus_cfg.staging_maxsize,
        queue_group=HUB_INBOUND,
    )
    return inbound_bus, inbound_bus_cfg


def _build_hub(deps: BuildHubDeps) -> Hub:
    """Construct HubConfig and Hub, wire stores and alias store."""
    cli_pool_cfg = _load_cli_pool_config(deps.raw_config)
    hub_cfg = _load_hub_config(deps.raw_config)
    pool_cfg = _load_pool_config(deps.raw_config)
    debouncer_cfg = _load_debouncer_config(deps.raw_config)
    event_bus_cfg = _load_event_bus_config(deps.raw_config)
    inbound_bus_cfg = _load_inbound_bus_config(deps.raw_config)
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

    # Wire TurnPublisher + ResumePublisherAdapter before Hub construction
    js = deps.inbound_bus._nc.jetstream()
    turn_publisher = TurnPublisher(js)
    adapter = TurnPublisherAdapter(turn_publisher, deps.stores.turn)

    hub = Hub(
        circuit_registry=deps.bundle.circuit_registry,
        msg_manager=deps.bundle.msg_manager,
        pairing_manager=deps.pm,
        stt=deps.voice.stt_service,
        tts=deps.voice.tts_service,
        prefs_store=deps.stores.prefs,
        event_bus=event_bus,
        inbound_bus=deps.inbound_bus,
        config=hub_config,
        resume_publisher=adapter,
    )
    hub.set_turn_store(deps.stores.turn)
    hub.set_message_index(deps.stores.message_index)

    # Wire alias_store (#472)
    deps.stores.prefs.set_alias_store(deps.stores.identity_alias)
    hub.set_alias_store(deps.stores.identity_alias)
    hub.set_turn_publisher(turn_publisher)

    return hub


async def build_cli_pool(
    raw_config: dict,
    agent_configs: dict[str, Agent],
    *,
    audit_sink: AuditSink | None = None,
) -> CliPool | None:
    """Build and start a CliPool if any agent uses the claude-cli backend."""
    cli_pool_cfg = _load_cli_pool_config(raw_config)
    for cfg in agent_configs.values():
        if cfg.llm_config.backend == "claude-cli":
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
            return cli_pool
    return None


def register_agents(  # noqa: PLR0913 — registration requires all deps
    hub: Hub,
    agent_configs: dict[str, Agent],
    cli_pool: CliPool | None,
    circuit_registry: CircuitRegistry,
    msg_manager: MessageManager,
    stt_service: STTProtocol | None,
    tts_service: TtsProtocol | None,
    agent_store: AgentStore | None,
    raw_config: dict,
    nats_llm_client: "LlmClient | None",
    *,
    cli_nats_driver: "LlmClient | None" = None,
) -> None:
    """Resolve agents from configs and register them on the hub."""
    llm_cfg = _load_llm_config(raw_config)
    all_agents = _resolve_agents(
        ResolveAgentsDeps(
            agent_configs=agent_configs,
            cli_pool=cli_pool,
            circuit_registry=circuit_registry,
            msg_manager=msg_manager,
            stt_service=stt_service,
            tts_service=tts_service,
            agent_store=agent_store,
            llm_cfg=llm_cfg,
            nats_llm_client=nats_llm_client,  # type: ignore[arg-type]  # T24 will update _resolve_agents to LlmClient
            cli_nats_driver=cli_nats_driver,
        )
    )
    for ag in all_agents.values():
        hub.register_agent(ag)


async def _init_clipool(
    nc: NATS,
    raw_config: dict,
    stores: StoreBundle,
) -> CliPoolBundle:
    """Build LlmClient (clipool), CliPool, CliPoolNatsWorker."""
    from lyra.adapters.clipool.clipool_worker import CliPoolNatsWorker

    cli_pool_cfg = _load_cli_pool_config(raw_config)
    audit_sink = JetStreamAuditSink()
    await audit_sink.provision(nc)

    cli_nats_driver = await build_llm_client(nc)
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


async def _build_hub_and_wire(  # noqa: PLR0913 — unavoidable wiring surface
    nc: NATS,
    raw_config: dict,
    stores: StoreBundle,
    *,
    circuit_registry: CircuitRegistry,
    bot_agent_map: dict[tuple[str, str], str],
    msg_manager: MessageManager,
    pm: PairingManager | None,
    inbound_bus: NatsBus[InboundMessage],
    freshness_drivers_list: list[object],
    agent_configs: dict[str, Agent],
    tg_bot_auths: list[tuple[TelegramBotConfig, Authenticator]],
    dc_bot_auths: list[tuple[DiscordBotConfig, Authenticator]],
    admin_user_ids: frozenset[str],
) -> tuple[
    Hub, list[NatsChannelProxy], list[OutboundDispatcher], LlmClient, LlmClient | None
]:
    """Build the Hub, wire NATS proxies, and register agents.

    Returns ``(hub, proxies, dispatchers, cli_nats_driver, nats_llm_client)``.
    """
    stt_service = init_nats_stt(nc)
    await stt_service.start()
    tts_service = init_nats_tts(nc)
    await tts_service.start()
    nats_llm_client = await init_nats_llm(nc)

    first_agent_config = agent_configs[next(iter(sorted(agent_configs)))]
    bundle = BotAuthBundle(
        tg_bot_auths=tg_bot_auths,
        dc_bot_auths=dc_bot_auths,
        bot_agent_map=bot_agent_map,
        agent_configs=agent_configs,
        first_agent_config=first_agent_config,
        msg_manager=msg_manager,
        circuit_registry=circuit_registry,
        admin_user_ids=admin_user_ids,
    )
    voice = VoiceBundle(
        stt_service=stt_service,
        tts_service=tts_service,
        nats_llm_client=nats_llm_client,
    )

    hub = _build_hub(
        BuildHubDeps(
            raw_config=raw_config,
            bundle=bundle,
            voice=voice,
            inbound_bus=inbound_bus,
            pm=pm,
            stores=stores,
            blob_store=init_blobstore(),
        )
    )
    if hub._turn_publisher is None:
        raise RuntimeError("TurnPublisher not wired — startup check failed")

    typing_publisher = TypingPublisher(nc)
    hub.set_typing_publisher(typing_publisher)

    cli_nats_driver = await build_llm_client(nc)
    cli_nats_driver.set_turn_store(stores.turn)
    hub.cli_pool = None

    freshness_drivers_list.extend(
        d for d in [cli_nats_driver, nats_llm_client] if d is not None
    )

    register_agents(
        hub,
        agent_configs,
        None,
        circuit_registry,
        msg_manager,
        stt_service,
        tts_service,
        stores.agent,
        raw_config,
        nats_llm_client,
        cli_nats_driver=cli_nats_driver,
    )

    tg_proxies, tg_dispatchers = wire_nats_telegram_proxies(
        NatsTgWiringDeps(
            hub=hub,
            nc=nc,
            tg_bot_auths=tg_bot_auths,
            bot_agent_map=bot_agent_map,
            circuit_registry=circuit_registry,
        )
    )
    dc_proxies, dc_dispatchers = wire_nats_discord_proxies(
        NatsDcWiringDeps(
            hub=hub,
            nc=nc,
            dc_bot_auths=dc_bot_auths,
            bot_agent_map=bot_agent_map,
            circuit_registry=circuit_registry,
        )
    )
    proxies = tg_proxies + dc_proxies
    dispatchers = tg_dispatchers + dc_dispatchers

    return hub, proxies, dispatchers, cli_nats_driver, nats_llm_client
