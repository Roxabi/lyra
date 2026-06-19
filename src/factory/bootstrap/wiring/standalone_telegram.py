"""Telegram standalone adapter wiring — NATS-connected without local Hub."""

from __future__ import annotations

import asyncio
import logging
import sys
from functools import partial
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
from factory.bootstrap.wiring.kv_bot_roster import seed_bot_roster
from factory.core.messaging.message import Platform
from roxabi_nats.readiness import wait_for_hub

log = logging.getLogger(__name__)


async def _close_tg_wired(label: str, wired: list[tuple]) -> None:
    """Close all wired Telegram adapters, buses, typing listeners, and consumers."""
    close_coros = [
        coro
        for a, ibus, tl, consumer in wired
        for coro in (a.close(), ibus.stop(), tl.stop(), consumer.stop())
    ]
    await close_safely(label, *close_coros)


async def _bootstrap_telegram_teardown(
    wired: list[tuple],
    stop: asyncio.Event,
) -> None:
    """Run shutdown sequence for all wired Telegram adapters."""
    poll_tasks = [
        asyncio.create_task(
            a.dp.start_polling(a.bot, handle_signals=False),
            name=f"telegram:{a._bot_id}",
        )
        for a, _, _, _ in wired
    ]
    try:
        await stop.wait()
        for a, _, _, _ in wired:
            await a.dp.stop_polling()
        await asyncio.gather(*poll_tasks, return_exceptions=True)
    finally:
        await _close_tg_wired("tg", wired)


async def bootstrap_telegram_standalone(
    nc: Any,
    raw_config: dict,
    config_bundle: AdapterConfigBundle,
    platform_enum: Platform,
    *,
    _stop: asyncio.Event | None = None,
) -> None:
    """Bootstrap a standalone Telegram adapter process connected to NATS."""
    js = nc.jetstream()
    blob_store = init_blobstore()

    wired: list[tuple] = []  # (TelegramAdapter, Bus, TypingListener, AudioConsumer)

    async def _wire_bot(bot_cfg: Any, token: str, webhook_secret: str | None) -> tuple:
        """Wire a single Telegram bot with NATS, typing listener, and audio consumer."""
        from factory.adapters.telegram import TelegramAdapter
        from factory.adapters.telegram.telegram import _telegram_scope_resolver
        from factory.adapters.telegram.telegram_outbound import _typing_worker

        bot_id = bot_cfg.bot_id

        def _tg_adapter_factory(inbound_bus: Any) -> tuple:
            adapter = TelegramAdapter(
                bot_id=bot_id,
                token=token,
                inbound_bus=inbound_bus,
                webhook_secret=webhook_secret or "",
                blob_store=blob_store,
            )
            typing_deps = TypingDeps(
                subject=f"factory.typing.telegram.{bot_id}",
                scope_resolver=_telegram_scope_resolver,
                worker_factory=partial(_typing_worker, adapter.bot),
            )
            return adapter, typing_deps

        return await wire_bot_common(
            nc=nc,
            platform_enum=platform_enum,
            bot_id=bot_id,
            adapter_factory=_tg_adapter_factory,
            config_bundle=config_bundle,
            js=js,
            blob_store=blob_store,
            resolve_identity=True,
        )

    # ADR-079 S3: wait_for_hub is a load-bearing barrier — it MUST precede
    # seed_bot_roster and start_audio_consumer (called inside wire_bot_common).
    await wait_for_hub(nc)

    tg_multi_cfg = await seed_bot_roster(js, "telegram")
    if not tg_multi_cfg.bots:
        sys.exit(
            "No telegram bots configured — roster comes from factory-state KV."
            " Run 'factory bot init' and ensure the hub published roster.telegram."
        )

    tg_creds: dict[str, tuple[str, str | None]] = {}
    for bot_cfg in tg_multi_cfg.bots:
        bot_id = bot_cfg.bot_id
        tg_creds[bot_id] = credentials.load_bot_token("telegram", bot_id)
        log.info("read token from /run/secrets/bot_token-%s", bot_id)

    for bot_cfg in tg_multi_cfg.bots:
        bot_id = bot_cfg.bot_id
        if bot_id not in tg_creds:
            continue
        token, webhook_secret = tg_creds[bot_id]

        try:
            wired_bot = await _wire_bot(bot_cfg, token, webhook_secret)
        except Exception:
            await _close_tg_wired("tg-wired", wired)
            raise

        wired.append(wired_bot)
        log.info(
            "adapter_standalone: Telegram bot_id=%s ready (NATS mode)",
            bot_id,
        )

    if not wired:
        sys.exit("No Telegram adapters started — check credentials")

    stop = setup_shutdown_event(_stop)
    try:
        await _bootstrap_telegram_teardown(wired, stop)
    finally:
        if blob_store is not None:
            await blob_store.aclose()  # type: ignore[union-attr]  # concrete HttpBlobStoreAdapter; aclose not on port