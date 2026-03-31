"""Standalone adapter bootstrap — runs TelegramAdapter or DiscordAdapter as a pure
NATS client, without a local Hub instance.

Entry point: _bootstrap_adapter_standalone(raw_config, platform)

Used by lyra_telegram and lyra_discord supervisor programs in NATS mode (ADR-037).
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Any

import nats

from lyra.adapters.nats_outbound_listener import NatsOutboundListener
from lyra.core.bus import Bus
from lyra.core.message import InboundMessage, InboundAudio, Platform
from lyra.core.bus import LocalBus

log = logging.getLogger(__name__)


async def _bootstrap_adapter_standalone(
    raw_config: dict,
    platform: str,
    *,
    _stop: asyncio.Event | None = None,
) -> None:
    """Bootstrap a standalone adapter process connected to NATS.

    Args:
        raw_config: Parsed config dict (lyra config.toml content).
        platform: "telegram" or "discord".
        _stop: Optional event for graceful shutdown (tests inject this).
    """
    nats_url = os.environ.get("NATS_URL")
    if not nats_url:
        sys.exit("NATS_URL required for standalone adapter mode")

    nc = await nats.connect(nats_url)
    log.info("adapter_standalone: connected to NATS at %s", nats_url)

    platform_enum = Platform(platform)

    from lyra.nats.nats_bus import NatsBus

    if platform == "telegram":
        from lyra.adapters.telegram import TelegramAdapter
        from lyra.config import TelegramMultiConfig

        tg_multi_cfg = TelegramMultiConfig.model_validate(raw_config.get("telegram", {}))
        if not tg_multi_cfg.bots:
            sys.exit("No telegram bots configured")

        for bot_cfg in tg_multi_cfg.bots:
            bot_id = bot_cfg.bot_id

            inbound_bus: Bus[InboundMessage] = NatsBus(  # type: ignore[type-arg]
                nc=nc,
                bot_id=bot_id,
                item_type=InboundMessage,
            )
            inbound_bus.register(platform_enum)
            await inbound_bus.start()

            inbound_audio_bus: Bus[InboundAudio] = LocalBus(name="inbound-audio")  # type: ignore[type-arg]

            token = os.environ.get(f"TELEGRAM_TOKEN_{bot_id.upper()}") or os.environ.get("TELEGRAM_TOKEN", "")
            webhook_secret = os.environ.get(f"TELEGRAM_WEBHOOK_SECRET_{bot_id.upper()}") or os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")

            adapter = TelegramAdapter(
                bot_id=bot_id,
                token=token,
                inbound_bus=inbound_bus,
                inbound_audio_bus=inbound_audio_bus,
                webhook_secret=webhook_secret,
            )
            await adapter.resolve_identity()

            listener = NatsOutboundListener(nc, platform_enum, bot_id, adapter)
            adapter._outbound_listener = listener
            await adapter.astart()

            log.info(
                "adapter_standalone: Telegram bot_id=%s ready (NATS mode)", bot_id
            )

            # Run uvicorn server
            try:
                import uvicorn
                config = uvicorn.Config(
                    adapter.app,
                    host="0.0.0.0",
                    port=int(os.environ.get("LYRA_WEBHOOK_PORT", "8080")),
                )
                server = uvicorn.Server(config)
                if _stop is not None:
                    # Test mode: run until stop event
                    serve_task = asyncio.create_task(server.serve())
                    await _stop.wait()
                    server.should_exit = True
                    await serve_task
                else:
                    await server.serve()
            finally:
                await adapter.close()
                await inbound_bus.stop()
                await nc.close()

    elif platform == "discord":
        from lyra.adapters.discord import DiscordAdapter
        from lyra.config import DiscordMultiConfig

        dc_multi_cfg = DiscordMultiConfig.model_validate(raw_config.get("discord", {}))
        if not dc_multi_cfg.bots:
            sys.exit("No discord bots configured")

        for bot_cfg in dc_multi_cfg.bots:
            bot_id = bot_cfg.bot_id

            inbound_bus_dc: Bus[InboundMessage] = NatsBus(  # type: ignore[type-arg]
                nc=nc,
                bot_id=bot_id,
                item_type=InboundMessage,
            )
            inbound_bus_dc.register(platform_enum)
            await inbound_bus_dc.start()

            inbound_audio_bus_dc: Bus[InboundAudio] = LocalBus(name="inbound-audio")  # type: ignore[type-arg]

            token = os.environ.get(f"DISCORD_TOKEN_{bot_id.upper()}") or os.environ.get("DISCORD_TOKEN", "")

            adapter_dc = DiscordAdapter(
                bot_id=bot_id,
                inbound_bus=inbound_bus_dc,
                inbound_audio_bus=inbound_audio_bus_dc,
                auto_thread=bot_cfg.auto_thread,
                thread_hot_hours=bot_cfg.thread_hot_hours,
            )

            listener_dc = NatsOutboundListener(nc, platform_enum, bot_id, adapter_dc)
            adapter_dc._outbound_listener = listener_dc
            await adapter_dc.astart()

            log.info(
                "adapter_standalone: Discord bot_id=%s ready (NATS mode)", bot_id
            )

            try:
                if _stop is not None:
                    start_task = asyncio.create_task(adapter_dc.start(token))
                    await _stop.wait()
                    await adapter_dc.close()
                    start_task.cancel()
                else:
                    await adapter_dc.start(token)
            finally:
                await inbound_bus_dc.stop()
                await nc.close()
    else:
        sys.exit(f"Unknown platform: {platform!r}")
