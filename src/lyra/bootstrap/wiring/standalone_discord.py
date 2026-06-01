"""Discord standalone adapter wiring — NATS-connected without local Hub."""

from __future__ import annotations

import asyncio
import logging
import sys
from functools import partial
from pathlib import Path
from typing import Any

from lyra.adapters.nats.nats_outbound_listener import ListenerDeps, NatsOutboundListener
from lyra.bootstrap import credentials
from lyra.bootstrap.factory.config import AdapterConfigBundle
from lyra.bootstrap.factory.voice_overlay import init_blobstore
from lyra.bootstrap.lifecycle.lifecycle_helpers import close_safely
from lyra.bootstrap.lifecycle.signal_handlers import setup_shutdown_event
from lyra.bootstrap.standalone.audio_consumer_bootstrap import start_audio_consumer
from lyra.bootstrap.wiring.bootstrap_wiring import wire_ingest
from lyra.core.messaging.bus import Bus
from lyra.core.messaging.message import InboundMessage, Platform
from lyra.nats.queue_groups import adapter_outbound
from roxabi_nats.readiness import wait_for_hub

log = logging.getLogger(__name__)


async def _bootstrap_discord_setup(
    raw_config: dict,
) -> tuple:
    """Load Discord config and credentials."""
    from lyra.config import DiscordMultiConfig

    dc_multi_cfg = DiscordMultiConfig.model_validate(raw_config.get("discord", {}))
    if not dc_multi_cfg.bots:
        sys.exit("No discord bots configured")

    dc_creds: dict[str, str] = {}
    for bot_cfg in dc_multi_cfg.bots:
        bot_id = bot_cfg.bot_id
        token, _ = credentials.load_bot_token("discord", bot_id)
        dc_creds[bot_id] = token
        log.info("read token from /run/secrets/bot_token-%s", bot_id)

    return dc_multi_cfg, dc_creds


async def _watch_kv_for_changes(
    kv: Any,
    bot_id: str,
    adapter: Any,
) -> None:
    """Background task: watch KV for watch_channels updates and mutate adapter."""
    from lyra.infrastructure.stores.bot_settings_kv import watch_watch_channels

    delay = 1.0
    while True:
        try:
            async for channels in watch_watch_channels(kv, bot_id):
                adapter._watch_channels = channels
                log.info(
                    "watch_channels updated for bot_id=%s: %s",
                    bot_id,
                    channels,
                )
            # Watcher ended normally; reset backoff and restart.
            delay = 1.0
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception(
                "watch_channels watcher failed for bot_id=%s, retrying in %ss",
                bot_id,
                delay,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)


async def _create_dc_stores(vault_dir: Path) -> tuple:
    """Create and connect Discord thread + turn stores."""
    from lyra.infrastructure.stores.thread_store import ThreadStore
    from lyra.infrastructure.stores.turn_store import TurnStore

    dc_thread_store = ThreadStore(db_path=vault_dir / "discord.db")
    await dc_thread_store.connect()
    dc_turn_store = TurnStore(db_path=vault_dir / "turns.db")
    await dc_turn_store.connect()
    return dc_thread_store, dc_turn_store


async def _close_dc_wired(label: str, wired_dc: list[tuple]) -> None:
    """Close all wired Discord adapters, buses, typing listeners, and consumers."""
    close_coros = [
        coro
        for a, _, ibus, tl, consumer in wired_dc
        for coro in (a.close(), ibus.stop(), tl.stop(), consumer.stop())
    ]
    await close_safely(label, *close_coros)


async def _bootstrap_discord_teardown(
    wired_dc: list[tuple],
    dc_thread_store: Any,
    dc_turn_store: Any,
    stop_dc: asyncio.Event,
) -> None:
    """Run shutdown sequence for all wired Discord adapters."""
    start_tasks = [
        asyncio.create_task(a.start(tok), name=f"discord:{a._bot_id}")
        for a, tok, _, _, _ in wired_dc
    ]
    try:
        await stop_dc.wait()
        await close_safely("dc-adapters", *[a.close() for a, _, _, _, _ in wired_dc])
        for t in start_tasks:
            t.cancel()
        await asyncio.gather(*start_tasks, return_exceptions=True)
    finally:
        dc_bus_coros = [ibus.stop() for _, _, ibus, _, _ in wired_dc]
        await close_safely("dc-buses", *dc_bus_coros)
        await close_safely("dc-typing", *[tl.stop() for _, _, _, tl, _ in wired_dc])
        await close_safely(
            "dc-audio", *[consumer.stop() for _, _, _, _, consumer in wired_dc]
        )
        await dc_thread_store.close()
        await dc_turn_store.close()


async def bootstrap_discord_standalone(  # noqa: PLR0915, C901 — bootstrap composition root
    nc: Any,
    raw_config: dict,
    config_bundle: AdapterConfigBundle,
    vault_dir: Path,
    platform_enum: Platform,
    *,
    _stop: asyncio.Event | None = None,
) -> None:
    """Bootstrap a standalone Discord adapter process connected to NATS."""
    dc_multi_cfg, dc_creds = await _bootstrap_discord_setup(raw_config)
    dc_thread_store, dc_turn_store = await _create_dc_stores(vault_dir)
    js = nc.jetstream()
    blob_store = init_blobstore()

    wired_dc: list[tuple] = []  # (DiscordAdapter, str, Bus, TypingListener, Consumer)
    _watch_tasks: list[asyncio.Task] = []

    async def _wire_bot(
        bot_cfg: Any,
        token: str,
        *,
        kv: Any,
    ) -> tuple:
        """Wire a single Discord bot with NATS, typing listener, and audio consumer."""
        bot_id = bot_cfg.bot_id

        from lyra.adapters.discord import DiscordAdapter
        from lyra.adapters.discord.adapter import _discord_scope_resolver
        from lyra.adapters.discord.discord_outbound import _discord_typing_worker
        from lyra.infrastructure.stores.bot_settings_kv import get_watch_channels
        from lyra.nats.nats_bus import NatsBus
        from lyra.typing import TypingListener, make_typing_factory

        inbound_bus_dc: Bus[InboundMessage] = NatsBus(
            nc=nc,
            bot_id=bot_id,
            item_type=InboundMessage,
            publish_only=True,
        )
        inbound_bus_dc.register(platform_enum)
        await inbound_bus_dc.start()

        watch_channels = await get_watch_channels(kv, bot_id)

        adapter_dc = DiscordAdapter(
            bot_id=bot_id,
            inbound_bus=inbound_bus_dc,
            auto_thread=bot_cfg.auto_thread,
            thread_hot_hours=bot_cfg.thread_hot_hours,
            thread_store=dc_thread_store,
            watch_channels=watch_channels,
            turn_store=dc_turn_store,
            blob_store=blob_store,
        )
        adapter_dc.configure_tool_display(config_bundle.tool_display)
        wire_ingest(adapter_dc, blob_store)

        # Background watcher: updates adapter._watch_channels dynamically.
        _watch_tasks.append(
            asyncio.create_task(
                _watch_kv_for_changes(kv, bot_id, adapter_dc),
                name=f"dc-watch:{bot_id}",
            )
        )

        listener_dc = NatsOutboundListener(
            ListenerDeps(
                nc=nc,
                platform=platform_enum,
                bot_id=bot_id,
                adapter=adapter_dc,
                queue_group=adapter_outbound(platform_enum.value, bot_id),
            )
        )
        adapter_dc._outbound_listener = listener_dc
        try:
            await adapter_dc.astart()
        except Exception:
            await close_safely(
                "dc-adapter-start",
                adapter_dc.close(),
                inbound_bus_dc.stop(),
            )
            raise

        dc_typing_listener = TypingListener(
            nc=nc,
            subject=f"lyra.typing.discord.{bot_id}",
            resolver=_discord_scope_resolver,
            factory_builder=make_typing_factory(
                partial(_discord_typing_worker, adapter_dc._resolve_channel)
            ),
            manager=adapter_dc._typing,
        )
        try:
            await dc_typing_listener.start()
        except Exception:
            await close_safely(
                "dc-typing-start",
                dc_typing_listener.stop(),
                adapter_dc.close(),
                inbound_bus_dc.stop(),
            )
            raise

        # Audio consumer: started strictly after astart() + typing, so no
        # cleanup needed in either astart or typing failure paths above.
        consumer = await start_audio_consumer(
            js, platform_enum.value, bot_id, adapter_dc
        )

        return (adapter_dc, token, inbound_bus_dc, dc_typing_listener, consumer)

    # ADR-079 S3: wait_for_hub is a load-bearing barrier — it MUST precede
    # start_audio_consumer (called inside _wire_bot). The hub sets hub.ready only
    # after ensure_stream + ensure_kv complete, so this call guarantees stream + KV
    # exist before any adapter bind/consume attempt. Moving it after the loop would
    # reintroduce the cold-boot race (BucketNotFoundError / missing-stream).
    await wait_for_hub(nc)

    # Bind bot-settings KV (hub-provisioned) before wiring bots.
    from lyra.infrastructure.stores.bot_settings_kv import ensure_kv as _ensure_bot_kv

    _bot_kv = await _ensure_bot_kv(js)

    for bot_cfg in dc_multi_cfg.bots:
        bot_id = bot_cfg.bot_id
        if bot_id not in dc_creds:
            continue
        token = dc_creds[bot_id]

        try:
            wired = await _wire_bot(bot_cfg, token, kv=_bot_kv)
        except Exception:
            await _close_dc_wired("dc-wired", wired_dc)
            for t in _watch_tasks:
                t.cancel()
            await dc_thread_store.close()
            await dc_turn_store.close()
            raise

        wired_dc.append(wired)
        log.info(
            "adapter_standalone: Discord bot_id=%s ready (NATS mode)",
            bot_id,
        )

    if not wired_dc:
        await dc_thread_store.close()
        await dc_turn_store.close()
        sys.exit("No Discord adapters started — check credentials")
    stop_dc = setup_shutdown_event(_stop)
    try:
        await _bootstrap_discord_teardown(
            wired_dc, dc_thread_store, dc_turn_store, stop_dc
        )
    finally:
        for t in _watch_tasks:
            t.cancel()
        if _watch_tasks:
            await asyncio.gather(*_watch_tasks, return_exceptions=True)
        if blob_store is not None:
            await blob_store.aclose()  # type: ignore[union-attr]  # concrete HttpBlobStoreAdapter; aclose not on port
