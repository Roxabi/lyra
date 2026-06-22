"""Hub-side NATS wiring for the web smoke adapter."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from factory.core.auth.authenticator import Authenticator, AuthenticatorDeps
from factory.core.auth.trust import TrustLevel
from factory.core.hub.hub_protocol import RoutingKey
from factory.core.hub.outbound.outbound_dispatcher import OutboundDispatcher
from factory.core.messaging.message import Platform
from factory.nats.nats_channel_proxy import NatsChannelProxy

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS

    from factory.core.agent import Agent
    from factory.core.hub import Hub
    from factory.core.lifecycle.circuit_breaker import CircuitRegistry

log = logging.getLogger(__name__)

WEB_BOT_ID = "smoke"
WEB_SCOPE_PREFIX = "agent:"


def wire_nats_web_smoke(
    *,
    hub: Hub,
    nc: NATS,
    agent_configs: dict[str, Agent],
    circuit_registry: CircuitRegistry,
) -> tuple[NatsChannelProxy, OutboundDispatcher]:
    """Register web smoke proxy, auth, and per-agent bindings."""
    platform = Platform.WEB
    bot_id = WEB_BOT_ID

    proxy = NatsChannelProxy(nc=nc, platform=platform, bot_id=bot_id)
    smoke_auth = Authenticator(
        AuthenticatorDeps(
            default=TrustLevel.TRUSTED, admin_user_ids=frozenset({"smoke"})
        )
    )
    hub.register_authenticator(platform, bot_id, smoke_auth)
    hub.register_adapter(platform, bot_id, proxy)

    for agent_name in sorted(agent_configs):
        scope_id = f"{WEB_SCOPE_PREFIX}{agent_name}"
        key = RoutingKey(platform, bot_id, scope_id)
        hub.register_binding(
            platform,
            bot_id,
            scope_id,
            agent_name,
            key.to_pool_id(),
        )
        log.info(
            "Registered web smoke binding: scope=%r agent=%r",
            scope_id,
            agent_name,
        )

    dispatcher = OutboundDispatcher(
        platform_name=platform.value,
        adapter=proxy,
        circuit=circuit_registry.get(platform.value),
        circuit_registry=circuit_registry,
        bot_id=bot_id,
    )
    hub.register_outbound_dispatcher(platform, bot_id, dispatcher)
    log.info(
        "Registered NATS proxy: web bot_id=%r agents=%d", bot_id, len(agent_configs)
    )
    return proxy, dispatcher
