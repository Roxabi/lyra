"""Discord standalone adapter wiring — NATS-connected without local Hub."""

from __future__ import annotations

import asyncio
import logging
import sys
from functools import partial
from pathlib import Path
from typing import Any

from factory.bootstrap import credentials
from factory.bootstrap.factory.config import AdapterConfigBundle
from factory.bootstrap.factory.voice_overlay import init_blobstore
from factory.bootstrap.lifecycle.lifecycle_helpers import close_safely
from factory.bootstrap.lifecycle.signal_handlers import setup_shutdown_event
from factory.bootstrap.wiring._standalone_wiring_common import (
    TypingDeps,
    wire_bot_common,
)
from factory.bootstrap.wiring.kv_watch_channels import seed_watch_channels
from factory.core.messaging.message import Platform
from factory.paths import factory_discord_data_dir
from roxabi_nats.readiness import wait_for_hub

log = logging.getLogger(__name__)


async def _bootstrap_discord_setup(raw_config: dict) -> tuple:
    """Load Discord config and credentials."""
    from factory.config import DiscordMultiConfig

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


async def _create_dc_stores(discord_dir: Path) -> tuple:
    """Create and connect Discord thread store (private dir) + KV last-session store."""
    from factory.infrastructure.stores.thread_store import ThreadStore

    dc_thread_store = ThreadStore(db_path=discord_dir / "discord.db")
    await dc_thread_store.connect()
    return (dc_thread_store,)


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


async def bootstrap_discord_standalone(  # noqa: PLR0915 — bootstrap composition root
    nc: Any,
    raw_config: dict,
    config_bundle: AdapterConfigBundle,
    platform_enum: Platform,
    *,
    _stop: asyncio.Event | None = None,
) -> None:
    """Bootstrap a standalone Discord adapter process connected to NATS."""
    dc_multi_cfg, dc_creds = await _bootstrap_discord_setup(raw_config)
    discord_dir = factory_discord_data_dir()
    discord_dir.mkdir(parents=True, exist_ok=True)
    (dc_thread_store,) = await _create_dc_stores(discord_dir)
    js = nc.jetstream()
    blob_store = init_blobstore()

    wired_dc: list[tuple] = []  # (DiscordAdapter, str, Bus, TypingListener, Consumer)

    async def _wire_bot(
        bot_cfg: Any, token: str, watch_channels: frozenset[int]
    ) -> tuple:
        """Wire a single Discord bot with NATS, typing listener, and audio consumer."""
        from factory.adapters.discord import DiscordAdapter
        from factory.adapters.discord.adapter import _discord_scope_resolver
        from factory.adapters.discord.discord_outbound import _discord_typing_worker

        bot_id = bot_cfg.bot_id

        def _dc_adapter_factory(inbound_bus: Any) -> tuple:
            adapter_dc = DiscordAdapter(
                bot_id=bot_id,
                inbound_bus=inbound_bus,
                auto_thread=bot_cfg.auto_thread,
                thread_hot_hours=bot_cfg.thread_hot_hours,
                thread_store=dc_thread_store,
                watch_channels=watch_channels,
                blob_store=blob_store,
            )
            typing_deps = TypingDeps(
                subject=f"factory.typing.discord.{bot_id}",
                scope_resolver=_discord_scope_resolver,
                worker_factory=partial(
                    _discord_typing_worker, adapter_dc._resolve_channel
                ),
            )
            return adapter_dc, typing_deps

        # wire_bot_common returns (adapter, inbound_bus, typing_listener, consumer).
        # Discord teardown needs the token for adapter.start(tok), so we extend the
        # tuple to (adapter, token, inbound_bus, typing_listener, consumer).
        (
            adapter_dc,
            inbound_bus_dc,
            dc_typing_listener,
            consumer,
        ) = await wire_bot_common(
            nc=nc,
            platform_enum=platform_enum,
            bot_id=bot_id,
            adapter_factory=_dc_adapter_factory,
            config_bundle=config_bundle,
            js=js,
            blob_store=blob_store,
            resolve_identity=False,
        )
        return (adapter_dc, token, inbound_bus_dc, dc_typing_listener, consumer)

    # ADR-079 S3: wait_for_hub is a load-bearing barrier — it MUST precede
    # start_audio_consumer (called inside wire_bot_common). The hub sets hub.ready only
    # after ensure_stream + ensure_kv complete, so this call guarantees stream + KV
    # exist before any adapter bind/consume attempt. Moving it after the loop would
    # reintroduce the cold-boot race (BucketNotFoundError / missing-stream).
    await wait_for_hub(nc)

    for bot_cfg in dc_multi_cfg.bots:
        bot_id = bot_cfg.bot_id
        if bot_id not in dc_creds:
            continue
        token = dc_creds[bot_id]

        try:
            # seed_watch_channels is inside the try so that any error
            # (e.g. NATS disconnect mid-loop) triggers the cleanup path (#21).
            watch_channels = await seed_watch_channels(js, "discord", bot_id)
            wired = await _wire_bot(bot_cfg, token, watch_channels)
        except Exception:
            await _close_dc_wired("dc-wired", wired_dc)
            await dc_thread_store.close()
            raise

        wired_dc.append(wired)
        log.info(
            "adapter_standalone: Discord bot_id=%s ready (NATS mode)",
            bot_id,
        )

    if not wired_dc:
        await dc_thread_store.close()
        sys.exit("No Discord adapters started — check credentials")
    stop_dc = setup_shutdown_event(_stop)
    try:
        await _bootstrap_discord_teardown(wired_dc, dc_thread_store, stop_dc)
    finally:
        if blob_store is not None:
            await blob_store.aclose()  # type: ignore[union-attr]  # concrete HttpBlobStoreAdapter; aclose not on port
