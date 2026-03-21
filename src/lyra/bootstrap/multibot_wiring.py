"""Adapter wiring and lifecycle helpers for multibot bootstrap."""

from __future__ import annotations

import asyncio
import logging
import os
import signal

import uvicorn

from lyra.adapters.discord import DiscordAdapter
from lyra.adapters.telegram import TelegramAdapter
from lyra.bootstrap.health import create_health_app
from lyra.config import DiscordBotConfig, TelegramBotConfig
from lyra.core.agent_store import AgentStore
from lyra.core.auth import AuthMiddleware
from lyra.core.circuit_breaker import CircuitRegistry
from lyra.core.cli_pool import CliPool
from lyra.core.credential_store import CredentialStore
from lyra.core.hub import Hub, RoutingKey
from lyra.core.message import Platform
from lyra.core.messages import MessageManager
from lyra.core.outbound_dispatcher import OutboundDispatcher
from lyra.core.pairing import PairingManager
from lyra.core.thread_store import ThreadStore
from lyra.errors import MissingCredentialsError

log = logging.getLogger(__name__)


async def wire_telegram_adapters(  # noqa: PLR0913 — wiring requires all deps
    hub: Hub,
    tg_bot_auths: list[tuple[TelegramBotConfig, AuthMiddleware]],
    bot_agent_map: dict[tuple[str, str], str],
    cred_store: CredentialStore,
    circuit_registry: CircuitRegistry,
    msg_manager: MessageManager,
) -> tuple[list[TelegramAdapter], list[OutboundDispatcher]]:
    """Wire each Telegram bot: adapter + dispatcher + hub bindings.

    Returns (adapters, dispatchers) lists.
    """
    adapters: list[TelegramAdapter] = []
    dispatchers: list[OutboundDispatcher] = []

    for bot_cfg, auth in tg_bot_auths:
        resolved_agent = bot_agent_map.get(("telegram", bot_cfg.bot_id))
        if resolved_agent is None:
            log.warning(
                "telegram bot_id=%r not in bot_agent_map — skipping adapter",
                bot_cfg.bot_id,
            )
            continue

        tg_creds = await cred_store.get_full("telegram", bot_cfg.bot_id)
        if tg_creds is None:
            raise MissingCredentialsError("telegram", bot_cfg.bot_id)
        tg_token, tg_webhook_secret = tg_creds

        adapter = TelegramAdapter(
            bot_id=bot_cfg.bot_id,
            token=tg_token,
            hub=hub,
            webhook_secret=tg_webhook_secret or "",
            circuit_registry=circuit_registry,
            msg_manager=msg_manager,
            auth=auth,
        )
        await adapter.resolve_identity()
        hub.register_adapter(Platform.TELEGRAM, bot_cfg.bot_id, adapter)

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
            adapter=adapter,
            circuit=circuit_registry.get("telegram"),
            circuit_registry=circuit_registry,
            bot_id=bot_cfg.bot_id,
        )
        hub.register_outbound_dispatcher(Platform.TELEGRAM, bot_cfg.bot_id, dispatcher)

        adapters.append(adapter)
        dispatchers.append(dispatcher)
        log.info(
            "Registered Telegram bot bot_id=%r agent=%r",
            bot_cfg.bot_id,
            resolved_agent,
        )

    return adapters, dispatchers


async def wire_discord_adapters(  # noqa: PLR0913 — wiring requires all deps
    hub: Hub,
    dc_bot_auths: list[tuple[DiscordBotConfig, AuthMiddleware]],
    bot_agent_map: dict[tuple[str, str], str],
    cred_store: CredentialStore,
    circuit_registry: CircuitRegistry,
    msg_manager: MessageManager,
    thread_store: ThreadStore,
    agent_store: AgentStore | None = None,
) -> tuple[
    list[tuple[DiscordAdapter, DiscordBotConfig, str]],
    list[OutboundDispatcher],
]:
    """Wire each Discord bot: adapter + dispatcher + hub bindings.

    Returns (adapters_with_config, dispatchers) where each adapter entry is
    (adapter, bot_cfg, token) — the token is needed later for ``adapter.start()``.
    """
    adapters: list[tuple[DiscordAdapter, DiscordBotConfig, str]] = []
    dispatchers: list[OutboundDispatcher] = []

    for bot_cfg, auth in dc_bot_auths:
        resolved_agent = bot_agent_map.get(("discord", bot_cfg.bot_id))
        if resolved_agent is None:
            log.warning(
                "discord bot_id=%r not in bot_agent_map — skipping adapter",
                bot_cfg.bot_id,
            )
            continue

        dc_creds = await cred_store.get_full("discord", bot_cfg.bot_id)
        if dc_creds is None:
            raise MissingCredentialsError("discord", bot_cfg.bot_id)
        dc_token, _ = dc_creds

        watch_channels: frozenset[int] = frozenset()
        if agent_store is not None:
            bot_settings = agent_store.get_bot_settings("discord", bot_cfg.bot_id)
            raw_ids = bot_settings.get("watch_channels", [])
            valid_ids: list[int] = []
            for ch in raw_ids:
                try:
                    valid_ids.append(int(ch))
                except (ValueError, TypeError):
                    log.warning(
                        "watch_channels: invalid channel id %r for bot %r — skipping",
                        ch,
                        bot_cfg.bot_id,
                    )
            watch_channels = frozenset(valid_ids)

        adapter = DiscordAdapter(
            hub=hub,
            bot_id=bot_cfg.bot_id,
            circuit_registry=circuit_registry,
            msg_manager=msg_manager,
            auto_thread=bot_cfg.auto_thread,
            thread_hot_hours=bot_cfg.thread_hot_hours,
            auth=auth,
            thread_store=thread_store,
            watch_channels=watch_channels,
        )
        hub.register_adapter(Platform.DISCORD, bot_cfg.bot_id, adapter)

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
            adapter=adapter,
            circuit=circuit_registry.get("discord"),
            circuit_registry=circuit_registry,
            bot_id=bot_cfg.bot_id,
        )
        hub.register_outbound_dispatcher(Platform.DISCORD, bot_cfg.bot_id, dispatcher)

        adapters.append((adapter, bot_cfg, dc_token))
        dispatchers.append(dispatcher)
        log.info(
            "Registered Discord bot bot_id=%r agent=%r",
            bot_cfg.bot_id,
            resolved_agent,
        )

    return adapters, dispatchers


async def run_lifecycle(  # noqa: PLR0913, C901 — lifecycle orchestration
    hub: Hub,
    tg_adapters: list,
    tg_dispatchers: list,
    dc_adapters: list[tuple],
    dc_dispatchers: list,
    pm: PairingManager | None,
    cli_pool: CliPool | None,
    _stop: asyncio.Event | None,
) -> None:
    """Start all buses/dispatchers/adapters, wait for stop, then tear down."""
    await hub.inbound_bus.start()
    await hub.inbound_audio_bus.start()
    for d in tg_dispatchers:
        await d.start()
    for d in dc_dispatchers:
        await d.start()

    health_port = int(os.environ.get("LYRA_HEALTH_PORT", "8443"))
    health_app = create_health_app(hub)
    health_config = uvicorn.Config(
        health_app, host="127.0.0.1", port=health_port, log_level="warning"
    )
    health_server = uvicorn.Server(health_config)

    stop = _stop if _stop is not None else asyncio.Event()
    if _stop is None:
        _loop = asyncio.get_running_loop()
        _loop.add_signal_handler(signal.SIGINT, stop.set)
        _loop.add_signal_handler(signal.SIGTERM, stop.set)

    from lyra.bootstrap.utils import _log_task_failure

    tasks = [
        asyncio.create_task(hub.run(), name="hub"),
        asyncio.create_task(hub._audio_pipeline.run(), name="hub-audio"),
        asyncio.create_task(health_server.serve(), name="health"),
    ]
    for tg_adapter in tg_adapters:
        tasks.append(
            asyncio.create_task(
                tg_adapter.dp.start_polling(tg_adapter.bot, handle_signals=False),
                name=f"telegram:{tg_adapter._bot_id}",
            )
        )
    for dc_adapter, dc_bot_cfg, dc_token in dc_adapters:
        _dc_task = asyncio.create_task(
            dc_adapter.start(dc_token),
            name=f"discord:{dc_bot_cfg.bot_id}",
        )
        _dc_task.add_done_callback(_log_task_failure)
        tasks.append(_dc_task)

    tg_active = [f"telegram:{a._bot_id}" for a in tg_adapters]
    dc_active = [f"discord:{c.bot_id}" for _, c, _ in dc_adapters]
    active = tg_active + dc_active
    log.info(
        "Lyra started — adapters: %s, health on :%d.",
        ", ".join(active) if active else "none",
        health_port,
    )

    await stop.wait()

    log.info("Shutdown signal received \u2014 stopping\u2026")
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await hub.inbound_bus.stop()
    await hub.inbound_audio_bus.stop()
    for d in tg_dispatchers:
        await d.stop()
    for d in dc_dispatchers:
        await d.stop()
    for dc_adapter, _, _dc_tok in dc_adapters:
        await dc_adapter.close()
    if pm is not None:
        await pm.close()
    if cli_pool is not None:
        active_ids = cli_pool.get_active_pool_ids()
        if active_ids:
            await hub.notify_shutdown_inflight(active_ids)
        await cli_pool.stop()
    log.info("Lyra stopped.")
