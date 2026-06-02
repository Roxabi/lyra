"""Adapter wiring and lifecycle helpers for multibot bootstrap."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lyra.core.ports.blobstore import BlobStorePort

from lyra.adapters.discord import DiscordAdapter
from lyra.adapters.telegram import TelegramAdapter
from lyra.bootstrap import credentials
from lyra.bootstrap.wiring.ingest_wiring import wire_ingest
from lyra.config import (
    DiscordBotConfig,
    TelegramBotConfig,
)
from lyra.core.auth.authenticator import Authenticator
from lyra.core.hub import Hub, OutboundDispatcher, RoutingKey
from lyra.core.lifecycle.circuit_breaker import CircuitRegistry
from lyra.core.messaging.message import Platform
from lyra.core.messaging.messages import MessageManager
from lyra.core.messaging.tool_display_config import ToolDisplayConfig
from lyra.infrastructure.stores.agent_store import AgentStore
from lyra.infrastructure.stores.thread_store import ThreadStore

# Default vault dir for discord.db (#417 / S4)
_DEFAULT_VAULT_DIR = os.path.expanduser("~/.lyra")

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DI containers
# ---------------------------------------------------------------------------


@dataclass
class TelegramWiringDeps:
    hub: Hub
    tg_bot_auths: list[tuple[TelegramBotConfig, Authenticator]]
    bot_agent_map: dict[tuple[str, str], str]
    circuit_registry: CircuitRegistry
    msg_manager: MessageManager
    nats_client: Any = None
    tool_display_config: ToolDisplayConfig | None = None
    blob_store: "BlobStorePort | None" = None


@dataclass
class DiscordWiringDeps:
    hub: Hub
    dc_bot_auths: list[tuple[DiscordBotConfig, Authenticator]]
    bot_agent_map: dict[tuple[str, str], str]
    circuit_registry: CircuitRegistry
    msg_manager: MessageManager
    agent_store: AgentStore | None = None
    vault_dir: str | None = None
    nats_client: Any = None
    tool_display_config: ToolDisplayConfig | None = None
    blob_store: "BlobStorePort | None" = None


# ---------------------------------------------------------------------------
# Wiring functions
# ---------------------------------------------------------------------------


async def wire_telegram_adapters(
    deps: TelegramWiringDeps,
) -> tuple[list[TelegramAdapter], list[OutboundDispatcher]]:
    """Wire each Telegram bot: adapter + dispatcher + hub bindings.

    Returns (adapters, dispatchers) lists.
    """
    adapters: list[TelegramAdapter] = []
    dispatchers: list[OutboundDispatcher] = []

    for bot_cfg, auth in deps.tg_bot_auths:
        resolved_agent = deps.bot_agent_map.get(("telegram", bot_cfg.bot_id))
        if resolved_agent is None:
            log.warning(
                "telegram bot_id=%r not in bot_agent_map — skipping adapter",
                bot_cfg.bot_id,
            )
            continue

        tg_token, tg_webhook_secret = credentials.load_bot_token(
            "telegram", bot_cfg.bot_id
        )

        adapter = TelegramAdapter(
            bot_id=bot_cfg.bot_id,
            token=tg_token,
            inbound_bus=deps.hub.inbound_bus,
            webhook_secret=tg_webhook_secret or "",
            circuit_registry=deps.circuit_registry,
            msg_manager=deps.msg_manager,
            turn_store=deps.hub._turn_store,
            blob_store=deps.blob_store,
        )
        adapter.configure_tool_display(deps.tool_display_config)
        adapter.configure_typing_publisher(deps.hub._typing_publisher)
        await adapter.resolve_identity()
        wire_ingest(adapter, deps.blob_store)
        # C3: Hub is the trust authority — register authenticator here, not on adapter.
        deps.hub.register_authenticator(Platform.TELEGRAM, bot_cfg.bot_id, auth)
        deps.hub.register_adapter(Platform.TELEGRAM, bot_cfg.bot_id, adapter)

        tg_key = RoutingKey(Platform.TELEGRAM, bot_cfg.bot_id, "*")
        deps.hub.register_binding(
            Platform.TELEGRAM,
            bot_cfg.bot_id,
            "*",
            resolved_agent,
            tg_key.to_pool_id(),
        )

        dispatcher = OutboundDispatcher(
            platform_name="telegram",
            adapter=adapter,
            circuit=deps.circuit_registry.get("telegram"),
            circuit_registry=deps.circuit_registry,
            bot_id=bot_cfg.bot_id,
        )
        deps.hub.register_outbound_dispatcher(
            Platform.TELEGRAM, bot_cfg.bot_id, dispatcher
        )

        adapters.append(adapter)
        dispatchers.append(dispatcher)
        log.info(
            "Registered Telegram bot bot_id=%r agent=%r",
            bot_cfg.bot_id,
            resolved_agent,
        )

    return adapters, dispatchers


async def wire_discord_adapters(
    deps: DiscordWiringDeps,
) -> tuple[
    list[tuple[DiscordAdapter, DiscordBotConfig, str]],
    list[OutboundDispatcher],
    ThreadStore | None,
]:
    """Wire each Discord bot: adapter + dispatcher + hub bindings.

    Returns (adapters_with_config, dispatchers) where each adapter entry is
    (adapter, bot_cfg, token) — the token is needed later for ``adapter.start()``.
    """
    adapters: list[tuple[DiscordAdapter, DiscordBotConfig, str]] = []
    dispatchers: list[OutboundDispatcher] = []

    # Shared ThreadStore for all Discord adapters (#417/S4)
    # One connection to discord.db — shared across all Discord bots.
    _vault = Path(
        deps.vault_dir or os.environ.get("LYRA_VAULT_DIR", _DEFAULT_VAULT_DIR)
    )
    thread_store: ThreadStore | None = None
    if deps.dc_bot_auths:
        thread_store = ThreadStore(db_path=_vault / "discord.db")
        await thread_store.connect()

    try:
        for bot_cfg, auth in deps.dc_bot_auths:
            resolved_agent = deps.bot_agent_map.get(("discord", bot_cfg.bot_id))
            if resolved_agent is None:
                log.warning(
                    "discord bot_id=%r not in bot_agent_map — skipping adapter",
                    bot_cfg.bot_id,
                )
                continue

            dc_token, _ = credentials.load_bot_token("discord", bot_cfg.bot_id)

            watch_channels: frozenset[int] = frozenset()
            if deps.agent_store is not None:
                bot_settings = deps.agent_store.get_bot_settings(
                    "discord", bot_cfg.bot_id
                )

                def _parse_channel_ids(key: str) -> frozenset[int]:
                    raw_ids = bot_settings.get(key, [])
                    valid: list[int] = []
                    for ch in raw_ids:
                        try:
                            valid.append(int(ch))
                        except (ValueError, TypeError):
                            log.warning(
                                "%s: invalid channel id %r for bot %r — skipping",
                                key,
                                ch,
                                bot_cfg.bot_id,
                            )
                    return frozenset(valid)

                watch_channels = _parse_channel_ids("watch_channels")

            adapter = DiscordAdapter(
                bot_id=bot_cfg.bot_id,
                inbound_bus=deps.hub.inbound_bus,
                circuit_registry=deps.circuit_registry,
                msg_manager=deps.msg_manager,
                auto_thread=bot_cfg.auto_thread,
                thread_hot_hours=bot_cfg.thread_hot_hours,
                thread_store=thread_store,
                watch_channels=watch_channels,
                turn_store=deps.hub._turn_store,
                blob_store=deps.blob_store,
            )
            adapter.configure_tool_display(deps.tool_display_config)
            adapter.configure_typing_publisher(deps.hub._typing_publisher)
            # Wire identity resolver for slash command trust (voice commands).
            adapter._resolve_identity_fn = deps.hub.resolve_identity
            wire_ingest(adapter, deps.blob_store)
            # C3: Hub is the trust authority — register here, not on adapter.
            deps.hub.register_authenticator(Platform.DISCORD, bot_cfg.bot_id, auth)
            deps.hub.register_adapter(Platform.DISCORD, bot_cfg.bot_id, adapter)

            dc_key = RoutingKey(Platform.DISCORD, bot_cfg.bot_id, "*")
            deps.hub.register_binding(
                Platform.DISCORD,
                bot_cfg.bot_id,
                "*",
                resolved_agent,
                dc_key.to_pool_id(),
            )

            dispatcher = OutboundDispatcher(
                platform_name="discord",
                adapter=adapter,
                circuit=deps.circuit_registry.get("discord"),
                circuit_registry=deps.circuit_registry,
                bot_id=bot_cfg.bot_id,
            )
            deps.hub.register_outbound_dispatcher(
                Platform.DISCORD, bot_cfg.bot_id, dispatcher
            )

            adapters.append((adapter, bot_cfg, dc_token))
            dispatchers.append(dispatcher)
            log.info(
                "Registered Discord bot bot_id=%r agent=%r",
                bot_cfg.bot_id,
                resolved_agent,
            )
    except Exception:
        # Close the shared ThreadStore on wiring failure (#417 fix)
        if thread_store is not None:
            await thread_store.close()
        raise

    return adapters, dispatchers, thread_store
