"""Telegram standalone adapter wiring — NATS-connected without local Hub."""

from __future__ import annotations

import asyncio
import logging
import sys
from functools import partial
from pathlib import Path
from typing import Any

from lyra.adapters.nats.nats_outbound_listener import NatsOutboundListener
from lyra.bootstrap import credentials
from lyra.bootstrap.factory.config import AdapterConfigBundle
from lyra.bootstrap.lifecycle.lifecycle_helpers import close_safely
from lyra.bootstrap.lifecycle.signal_handlers import setup_shutdown_event
from lyra.core.messaging.bus import Bus
from lyra.core.messaging.message import InboundMessage, Platform
from lyra.nats.queue_groups import adapter_outbound
from roxabi_nats.readiness import wait_for_hub

log = logging.getLogger(__name__)


async def _bootstrap_telegram_setup(
    raw_config: dict,
    vault_dir: Path,
) -> tuple:
    """Load Telegram config, credentials, and connect turn store."""
    from lyra.config import TelegramMultiConfig
    from lyra.infrastructure.stores.turn_store import TurnStore

    tg_multi_cfg = TelegramMultiConfig.model_validate(raw_config.get("telegram", {}))
    if not tg_multi_cfg.bots:
        sys.exit("No telegram bots configured")

    tg_creds: dict[str, tuple[str, str | None]] = {}
    for bot_cfg in tg_multi_cfg.bots:
        bot_id = bot_cfg.bot_id
        tg_creds[bot_id] = credentials.load_bot_token("telegram", bot_id)
        log.info("read token from /run/secrets/bot_token-%s", bot_id)

    tg_turn_store = TurnStore(db_path=vault_dir / "turns.db")
    await tg_turn_store.connect()

    return tg_multi_cfg, tg_creds, tg_turn_store


async def _close_tg_wired(label: str, wired: list[tuple]) -> None:
    """Close all wired Telegram adapters, buses, and typing listeners."""
    close_coros = [
        coro for a, ibus, tl in wired for coro in (a.close(), ibus.stop(), tl.stop())
    ]
    await close_safely(label, *close_coros)


async def _bootstrap_telegram_teardown(
    wired: list[tuple],
    tg_turn_store: Any,
    stop: asyncio.Event,
) -> None:
    """Run shutdown sequence for all wired Telegram adapters."""
    poll_tasks = [
        asyncio.create_task(
            a.dp.start_polling(a.bot, handle_signals=False),
            name=f"telegram:{a._bot_id}",
        )
        for a, _, _ in wired
    ]
    try:
        await stop.wait()
        for a, _, _ in wired:
            await a.dp.stop_polling()
        await asyncio.gather(*poll_tasks, return_exceptions=True)
    finally:
        await _close_tg_wired("tg", wired)
        await tg_turn_store.close()


async def bootstrap_telegram_standalone(
    nc: Any,
    raw_config: dict,
    config_bundle: AdapterConfigBundle,
    vault_dir: Path,
    platform_enum: Platform,
    *,
    _stop: asyncio.Event | None = None,
) -> None:
    """Bootstrap a standalone Telegram adapter process connected to NATS."""
    tg_multi_cfg, tg_creds, tg_turn_store = await _bootstrap_telegram_setup(
        raw_config, vault_dir
    )

    wired: list[tuple] = []  # (TelegramAdapter, Bus, TypingListener)

    async def _wire_bot(bot_cfg: Any, token: str, webhook_secret: str | None) -> tuple:
        """Wire a single Telegram bot with NATS transport and typing listener."""
        bot_id = bot_cfg.bot_id

        from lyra.adapters.telegram import TelegramAdapter
        from lyra.adapters.telegram.telegram import _telegram_scope_resolver
        from lyra.adapters.telegram.telegram_outbound import _typing_worker
        from lyra.nats.nats_bus import NatsBus
        from lyra.typing import TypingListener, make_typing_factory

        inbound_bus: Bus[InboundMessage] = NatsBus(
            nc=nc,
            bot_id=bot_id,
            item_type=InboundMessage,
            publish_only=True,
        )
        inbound_bus.register(platform_enum)
        await inbound_bus.start()

        adapter = TelegramAdapter(
            bot_id=bot_id,
            token=token,
            inbound_bus=inbound_bus,
            webhook_secret=webhook_secret or "",
            turn_store=tg_turn_store,
            tool_display_config=config_bundle.tool_display,
        )
        await adapter.resolve_identity()

        listener = NatsOutboundListener(
            nc,
            platform_enum,
            bot_id,
            adapter,
            queue_group=adapter_outbound(platform_enum.value, bot_id),
        )
        adapter._outbound_listener = listener
        try:
            await adapter.astart()
        except Exception:
            await close_safely(
                "tg-adapter-start",
                adapter.close(),
                inbound_bus.stop(),
            )
            raise

        tg_typing_listener = TypingListener(
            nc=nc,
            subject=f"lyra.typing.telegram.{bot_id}",
            resolver=_telegram_scope_resolver,
            factory_builder=make_typing_factory(partial(_typing_worker, adapter.bot)),
            manager=adapter._typing,
        )
        try:
            await tg_typing_listener.start()
        except Exception:
            await close_safely(
                "tg-typing-start",
                tg_typing_listener.stop(),
                adapter.close(),
                inbound_bus.stop(),
            )
            raise

        return (adapter, inbound_bus, tg_typing_listener)

    for bot_cfg in tg_multi_cfg.bots:
        bot_id = bot_cfg.bot_id
        if bot_id not in tg_creds:
            continue
        token, webhook_secret = tg_creds[bot_id]

        try:
            wired_bot = await _wire_bot(bot_cfg, token, webhook_secret)
        except Exception:
            await _close_tg_wired("tg-wired", wired)
            await tg_turn_store.close()
            raise

        wired.append(wired_bot)
        log.info(
            "adapter_standalone: Telegram bot_id=%s ready (NATS mode)",
            bot_id,
        )

    if not wired:
        await tg_turn_store.close()
        sys.exit("No Telegram adapters started — check credentials")
    await wait_for_hub(nc)

    stop = setup_shutdown_event(_stop)
    await _bootstrap_telegram_teardown(wired, tg_turn_store, stop)
