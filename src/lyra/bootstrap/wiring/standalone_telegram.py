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


async def bootstrap_telegram_standalone(  # noqa: PLR0915
    nc: Any,
    raw_config: dict,
    config_bundle: AdapterConfigBundle,
    vault_dir: Path,
    platform_enum: Platform,
    *,
    _stop: asyncio.Event | None = None,
) -> None:
    """Bootstrap a standalone Telegram adapter process connected to NATS."""
    from lyra.adapters.telegram import TelegramAdapter
    from lyra.config import TelegramMultiConfig
    from lyra.infrastructure.stores.turn_store import TurnStore
    from lyra.nats.nats_bus import NatsBus

    tg_multi_cfg = TelegramMultiConfig.model_validate(
        raw_config.get("telegram", {})
    )
    if not tg_multi_cfg.bots:
        sys.exit("No telegram bots configured")

    tg_creds: dict[str, tuple[str, str | None]] = {}
    for bot_cfg in tg_multi_cfg.bots:
        bot_id = bot_cfg.bot_id
        tg_creds[bot_id] = credentials.load_bot_token("telegram", bot_id)
        log.info("read token from /run/secrets/bot_token-%s", bot_id)

    tg_turn_store = TurnStore(db_path=vault_dir / "turns.db")
    await tg_turn_store.connect()

    wired: list[tuple] = []  # (TelegramAdapter, Bus, TypingListener)

    for bot_cfg in tg_multi_cfg.bots:
        bot_id = bot_cfg.bot_id
        if bot_id not in tg_creds:
            continue
        token, webhook_secret = tg_creds[bot_id]

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
            await close_safely(
                "tg-wired",
                *[
                    coro
                    for a, ibus, tl in wired
                    for coro in (a.close(), ibus.stop(), tl.stop())
                ],
            )
            await tg_turn_store.close()
            raise

        from lyra.adapters.telegram.telegram import _telegram_scope_resolver
        from lyra.adapters.telegram.telegram_outbound import _typing_worker
        from lyra.typing import TypingListener, make_typing_factory

        tg_typing_listener = TypingListener(
            nc=nc,
            subject=f"lyra.typing.telegram.{bot_id}",
            resolver=_telegram_scope_resolver,
            factory_builder=make_typing_factory(
                partial(_typing_worker, adapter.bot)
            ),
            manager=adapter._typing,
        )
        await tg_typing_listener.start()

        wired.append((adapter, inbound_bus, tg_typing_listener))
        log.info(
            "adapter_standalone: Telegram bot_id=%s ready (NATS mode)",
            bot_id,
        )

    if not wired:
        sys.exit("No Telegram adapters started — check credentials")
    await wait_for_hub(nc)

    stop = setup_shutdown_event(_stop)

    poll_tasks = [
        asyncio.create_task(
            a.dp.start_polling(a.bot, handle_signals=False),
            name=f"telegram:{a._bot_id}",
        )
        for a, _, _tl in wired
    ]
    try:
        await stop.wait()
        for a, _, _tl in wired:
            await a.dp.stop_polling()
        await asyncio.gather(*poll_tasks, return_exceptions=True)
    finally:
        await close_safely(
            "tg",
            *[
                coro
                for a, ibus, tl in wired
                for coro in (a.close(), ibus.stop(), tl.stop())
            ],
        )
        await tg_turn_store.close()
