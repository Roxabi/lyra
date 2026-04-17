"""Hub construction helpers for standalone Hub bootstrap.

Extracted from hub_standalone.py for size compliance (#760).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from nats.aio.client import Client as NATS

from lyra.bootstrap.agent_factory import _resolve_agents
from lyra.bootstrap.config import (
    InboundBusConfig,
    _load_cli_pool_config,
    _load_debouncer_config,
    _load_event_bus_config,
    _load_hub_config,
    _load_inbound_bus_config,
    _load_llm_config,
    _load_pool_config,
)
from lyra.core.agent import Agent
from lyra.core.circuit_breaker import CircuitRegistry
from lyra.core.cli_pool import CliPool
from lyra.core.hub import Hub
from lyra.core.hub.event_bus import PipelineEventBus
from lyra.core.message import InboundMessage
from lyra.infrastructure.stores.agent_store import AgentStore
from lyra.nats.nats_bus import NatsBus
from lyra.nats.queue_groups import HUB_INBOUND
from lyra.stt import STTProtocol
from lyra.tts import TtsProtocol

if TYPE_CHECKING:
    from lyra.core.messages import MessageManager
    from lyra.core.stores.pairing import PairingManager
    from lyra.core.stores.prefs_store import PrefsStore
    from lyra.llm.drivers.nats_driver import NatsLlmDriver

log = logging.getLogger(__name__)


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


def build_hub(  # noqa: PLR0913 — construction requires all deps
    raw_config: dict,
    *,
    circuit_registry: CircuitRegistry,
    msg_manager: MessageManager | None,
    pairing_manager: PairingManager | None,
    stt_service: STTProtocol | None,
    tts_service: TtsProtocol | None,
    prefs_store: PrefsStore | None,
    inbound_bus: NatsBus[InboundMessage],
    inbound_bus_cfg: InboundBusConfig,
) -> Hub:
    """Construct a Hub from loaded config and injected dependencies."""
    cli_pool_cfg = _load_cli_pool_config(raw_config)
    hub_cfg = _load_hub_config(raw_config)
    pool_cfg = _load_pool_config(raw_config)
    debouncer_cfg = _load_debouncer_config(raw_config)
    event_bus_cfg = _load_event_bus_config(raw_config)
    event_bus = PipelineEventBus(maxsize=event_bus_cfg.queue_maxsize)

    hub = Hub(
        circuit_registry=circuit_registry,
        msg_manager=msg_manager,
        pairing_manager=pairing_manager,
        stt=stt_service,
        tts=tts_service,
        debounce_ms=debouncer_cfg.default_debounce_ms,
        cancel_on_new_message=debouncer_cfg.cancel_on_new_message,
        prefs_store=prefs_store,
        turn_timeout=cli_pool_cfg.turn_timeout,
        pool_ttl=hub_cfg.pool_ttl,
        rate_limit=hub_cfg.rate_limit,
        rate_window=hub_cfg.rate_window,
        max_sdk_history=pool_cfg.max_sdk_history,
        safe_dispatch_timeout=pool_cfg.safe_dispatch_timeout,
        staging_maxsize=inbound_bus_cfg.staging_maxsize,
        platform_queue_maxsize=inbound_bus_cfg.platform_queue_maxsize,
        queue_depth_threshold=inbound_bus_cfg.queue_depth_threshold,
        max_merged_chars=debouncer_cfg.max_merged_chars,
        event_bus=event_bus,
        inbound_bus=inbound_bus,
    )
    return hub


async def build_cli_pool(
    raw_config: dict, agent_configs: dict[str, Agent]
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
    nats_llm_driver: NatsLlmDriver | None,
) -> None:
    """Resolve agents from configs and register them on the hub."""
    llm_cfg = _load_llm_config(raw_config)
    all_agents = _resolve_agents(
        agent_configs,
        cli_pool,
        circuit_registry,
        msg_manager,
        stt_service,
        tts_service,
        agent_store=agent_store,
        llm_cfg=llm_cfg,
        nats_llm_driver=nats_llm_driver,
    )
    for ag in all_agents.values():
        hub.register_agent(ag)
