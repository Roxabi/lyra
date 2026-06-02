"""Construct Hub and wire stores / alias_store."""

from __future__ import annotations

import logging

from factory.bootstrap.factory.config import (
    _load_cli_pool_config,
    _load_debouncer_config,
    _load_event_bus_config,
    _load_hub_config,
    _load_inbound_bus_config,
    _load_pool_config,
)
from factory.bootstrap.types import BuildHubDeps
from factory.core.config import HubConfig
from factory.core.hub import Hub
from factory.core.hub.event_bus import PipelineEventBus
from factory.infrastructure.resume_publisher_adapter import TurnPublisherAdapter
from factory.transport.turn_publisher import TurnPublisher
from factory.transport.typing_publisher import TypingPublisher

log = logging.getLogger(__name__)


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

    typing_publisher = TypingPublisher(deps.inbound_bus._nc)
    hub.set_typing_publisher(typing_publisher)

    return hub
