"""NATS adapter wiring for standalone Hub — NatsChannelProxy + OutboundDispatcher setup.

Extracted from hub_standalone.py for size compliance (#760).
Unified parameterized wiring function introduced in #1663.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Union

from nats.aio.client import Client as NATS

from factory.config import DiscordBotConfig, TelegramBotConfig
from factory.core.auth.authenticator import Authenticator
from factory.core.hub import Hub
from factory.core.hub.hub_protocol import RoutingKey
from factory.core.hub.outbound.outbound_dispatcher import OutboundDispatcher
from factory.core.lifecycle.circuit_breaker import CircuitRegistry
from factory.core.messaging.message import Platform
from factory.nats.nats_channel_proxy import NatsChannelProxy

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

# Unified bot config type — both share a ``bot_id`` attribute.
_BotCfg = Union[TelegramBotConfig, DiscordBotConfig]


# ---------------------------------------------------------------------------
# DI containers
# ---------------------------------------------------------------------------


@dataclass
class NatsProxyWiringDeps:
    """Unified wiring deps for a single platform (Telegram or Discord)."""

    hub: Hub
    nc: NATS
    platform: Platform
    bot_auths: list[tuple[_BotCfg, Authenticator]]
    bot_agent_map: dict[tuple[str, str], str]
    circuit_registry: CircuitRegistry


@dataclass
class NatsTgWiringDeps:
    """Deprecated — use NatsProxyWiringDeps with platform=Platform.TELEGRAM."""

    hub: Hub
    nc: NATS
    tg_bot_auths: list[tuple[TelegramBotConfig, Authenticator]]
    bot_agent_map: dict[tuple[str, str], str]
    circuit_registry: CircuitRegistry


@dataclass
class NatsDcWiringDeps:
    """Deprecated — use NatsProxyWiringDeps with platform=Platform.DISCORD."""

    hub: Hub
    nc: NATS
    dc_bot_auths: list[tuple[DiscordBotConfig, Authenticator]]
    bot_agent_map: dict[tuple[str, str], str]
    circuit_registry: CircuitRegistry


# ---------------------------------------------------------------------------
# Wiring functions
# ---------------------------------------------------------------------------


def wire_nats_proxies(
    deps: NatsProxyWiringDeps,
) -> tuple[list[NatsChannelProxy], list[OutboundDispatcher]]:
    """Wire each bot of *deps.platform* to a NatsChannelProxy + OutboundDispatcher.

    Returns (proxies, dispatchers) lists.
    """
    platform = deps.platform
    platform_name = platform.value.lower()

    proxies: list[NatsChannelProxy] = []
    dispatchers: list[OutboundDispatcher] = []

    for bot_cfg, auth in deps.bot_auths:
        resolved_agent = deps.bot_agent_map.get((platform_name, bot_cfg.bot_id))
        if resolved_agent is None:
            log.warning(
                "%s bot_id=%r not in bot_agent_map — skipping",
                platform_name,
                bot_cfg.bot_id,
            )
            continue

        proxy = NatsChannelProxy(nc=deps.nc, platform=platform, bot_id=bot_cfg.bot_id)
        proxies.append(proxy)
        deps.hub.register_authenticator(platform, bot_cfg.bot_id, auth)
        deps.hub.register_adapter(platform, bot_cfg.bot_id, proxy)

        key = RoutingKey(platform, bot_cfg.bot_id, "*")
        deps.hub.register_binding(
            platform,
            bot_cfg.bot_id,
            "*",
            resolved_agent,
            key.to_pool_id(),
        )

        dispatcher = OutboundDispatcher(
            platform_name=platform_name,
            adapter=proxy,
            circuit=deps.circuit_registry.get(platform_name),
            circuit_registry=deps.circuit_registry,
            bot_id=bot_cfg.bot_id,
        )
        deps.hub.register_outbound_dispatcher(platform, bot_cfg.bot_id, dispatcher)
        dispatchers.append(dispatcher)
        log.info(
            "Registered NATS proxy: %s bot_id=%r agent=%r",
            platform_name,
            bot_cfg.bot_id,
            resolved_agent,
        )

    return proxies, dispatchers


def wire_nats_telegram_proxies(
    deps: NatsTgWiringDeps,
) -> tuple[list[NatsChannelProxy], list[OutboundDispatcher]]:
    """Deprecated thin alias — use wire_nats_proxies instead."""
    return wire_nats_proxies(
        NatsProxyWiringDeps(
            hub=deps.hub,
            nc=deps.nc,
            platform=Platform.TELEGRAM,
            bot_auths=deps.tg_bot_auths,
            bot_agent_map=deps.bot_agent_map,
            circuit_registry=deps.circuit_registry,
        )
    )


def wire_nats_discord_proxies(
    deps: NatsDcWiringDeps,
) -> tuple[list[NatsChannelProxy], list[OutboundDispatcher]]:
    """Deprecated thin alias — use wire_nats_proxies instead."""
    return wire_nats_proxies(
        NatsProxyWiringDeps(
            hub=deps.hub,
            nc=deps.nc,
            platform=Platform.DISCORD,
            bot_auths=deps.dc_bot_auths,
            bot_agent_map=deps.bot_agent_map,
            circuit_registry=deps.circuit_registry,
        )
    )
