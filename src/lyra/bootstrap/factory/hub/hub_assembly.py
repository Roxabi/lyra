"""Build Hub, wire NATS proxies, and register agents."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from nats.aio.client import Client as NATS

from lyra.bootstrap.factory.hub.hub_agent_registration import register_agents
from lyra.bootstrap.factory.hub.hub_core import _build_hub
from lyra.bootstrap.factory.hub.hub_llm_client import build_llm_client
from lyra.bootstrap.factory.llm_overlay import init_nats_llm
from lyra.bootstrap.factory.voice_overlay import init_nats_stt, init_nats_tts
from lyra.bootstrap.types import BotAuthBundle, BuildHubDeps, VoiceBundle
from lyra.bootstrap.wiring.nats_wiring import (
    NatsDcWiringDeps,
    NatsTgWiringDeps,
    wire_nats_discord_proxies,
    wire_nats_telegram_proxies,
)
from lyra.core.agent import Agent
from lyra.core.hub import Hub
from lyra.core.lifecycle.circuit_breaker import CircuitRegistry
from lyra.core.messaging.message import InboundMessage
from lyra.core.messaging.messages import MessageManager
from lyra.infrastructure.stores.pairing import PairingManager
from lyra.nats.nats_bus import NatsBus

if TYPE_CHECKING:
    from lyra.bootstrap.bootstrap_stores import StoreBundle
    from lyra.bootstrap.wiring.bootstrap_wiring import Authenticator
    from lyra.config import DiscordBotConfig, TelegramBotConfig
    from lyra.core.hub import OutboundDispatcher
    from lyra.llm.llm_client import LlmClient
    from lyra.nats.nats_channel_proxy import NatsChannelProxy

log = logging.getLogger(__name__)


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
        )
    )
    if hub._turn_publisher is None:
        raise RuntimeError("TurnPublisher not wired — startup check failed")

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
