"""NATS adapter wiring for standalone Hub — NatsChannelProxy + OutboundDispatcher setup.

Extracted from hub_standalone.py for size compliance (#760).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from nats.aio.client import Client as NATS

from lyra.config import DiscordBotConfig, TelegramBotConfig
from lyra.core.authenticator import Authenticator
from lyra.core.circuit_breaker import CircuitRegistry
from lyra.core.hub import Hub
from lyra.core.hub.hub_protocol import RoutingKey
from lyra.core.hub.outbound_dispatcher import OutboundDispatcher
from lyra.core.message import Platform
from lyra.nats.nats_channel_proxy import NatsChannelProxy

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)


def wire_nats_telegram_proxies(  # noqa: PLR0913 — wiring requires all deps
    hub: Hub,
    nc: NATS,
    tg_bot_auths: list[tuple[TelegramBotConfig, Authenticator]],
    bot_agent_map: dict[tuple[str, str], str],
    circuit_registry: CircuitRegistry,
) -> tuple[list[NatsChannelProxy], list[OutboundDispatcher]]:
    """Wire each Telegram bot to a NatsChannelProxy + OutboundDispatcher.

    Returns (proxies, dispatchers) lists.
    """
    proxies: list[NatsChannelProxy] = []
    dispatchers: list[OutboundDispatcher] = []

    for bot_cfg, auth in tg_bot_auths:
        resolved_agent = bot_agent_map.get(("telegram", bot_cfg.bot_id))
        if resolved_agent is None:
            log.warning(
                "telegram bot_id=%r not in bot_agent_map — skipping",
                bot_cfg.bot_id,
            )
            continue

        proxy = NatsChannelProxy(
            nc=nc, platform=Platform.TELEGRAM, bot_id=bot_cfg.bot_id
        )
        proxies.append(proxy)
        hub.register_authenticator(Platform.TELEGRAM, bot_cfg.bot_id, auth)
        hub.register_adapter(Platform.TELEGRAM, bot_cfg.bot_id, proxy)

        tg_key = RoutingKey(Platform.TELEGRAM, bot_cfg.bot_id, "*")
        hub.register_binding(
            Platform.TELEGRAM,
            bot_cfg.bot_id,
            "*",
            resolved_agent,
            tg_key.to_pool_id(),
        )

        dispatcher = OutboundDispatcher(
            platform_name="telegram",
            adapter=proxy,
            circuit=circuit_registry.get("telegram"),
            circuit_registry=circuit_registry,
            bot_id=bot_cfg.bot_id,
        )
        hub.register_outbound_dispatcher(Platform.TELEGRAM, bot_cfg.bot_id, dispatcher)
        dispatchers.append(dispatcher)
        log.info(
            "Registered NATS proxy: telegram bot_id=%r agent=%r",
            bot_cfg.bot_id,
            resolved_agent,
        )

    return proxies, dispatchers


def wire_nats_discord_proxies(  # noqa: PLR0913 — wiring requires all deps
    hub: Hub,
    nc: NATS,
    dc_bot_auths: list[tuple[DiscordBotConfig, Authenticator]],
    bot_agent_map: dict[tuple[str, str], str],
    circuit_registry: CircuitRegistry,
) -> tuple[list[NatsChannelProxy], list[OutboundDispatcher]]:
    """Wire each Discord bot to a NatsChannelProxy + OutboundDispatcher.

    Returns (proxies, dispatchers) lists.
    """
    proxies: list[NatsChannelProxy] = []
    dispatchers: list[OutboundDispatcher] = []

    for bot_cfg, auth in dc_bot_auths:
        resolved_agent = bot_agent_map.get(("discord", bot_cfg.bot_id))
        if resolved_agent is None:
            log.warning(
                "discord bot_id=%r not in bot_agent_map — skipping",
                bot_cfg.bot_id,
            )
            continue

        proxy = NatsChannelProxy(
            nc=nc, platform=Platform.DISCORD, bot_id=bot_cfg.bot_id
        )
        proxies.append(proxy)
        hub.register_authenticator(Platform.DISCORD, bot_cfg.bot_id, auth)
        hub.register_adapter(Platform.DISCORD, bot_cfg.bot_id, proxy)

        dc_key = RoutingKey(Platform.DISCORD, bot_cfg.bot_id, "*")
        hub.register_binding(
            Platform.DISCORD,
            bot_cfg.bot_id,
            "*",
            resolved_agent,
            dc_key.to_pool_id(),
        )

        dispatcher = OutboundDispatcher(
            platform_name="discord",
            adapter=proxy,
            circuit=circuit_registry.get("discord"),
            circuit_registry=circuit_registry,
            bot_id=bot_cfg.bot_id,
        )
        hub.register_outbound_dispatcher(Platform.DISCORD, bot_cfg.bot_id, dispatcher)
        dispatchers.append(dispatcher)
        log.info(
            "Registered NATS proxy: discord bot_id=%r agent=%r",
            bot_cfg.bot_id,
            resolved_agent,
        )

    return proxies, dispatchers
