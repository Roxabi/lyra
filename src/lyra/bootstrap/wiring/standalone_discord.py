"""Discord standalone adapter wiring — NATS-connected without local Hub."""

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


async def _bootstrap_discord_setup(
    raw_config: dict,
    vault_dir: Path,
) -> tuple:
    """Load Discord config, credentials, and watch channels."""
    from lyra.config import DiscordMultiConfig
    from lyra.infrastructure.stores.agent_store import AgentStore

    dc_multi_cfg = DiscordMultiConfig.model_validate(
        raw_config.get("discord", {})
    )
    if not dc_multi_cfg.bots:
        sys.exit("No discord bots configured")

    dc_creds: dict[str, str] = {}
    for bot_cfg in dc_multi_cfg.bots:
        bot_id = bot_cfg.bot_id
        token, _ = credentials.load_bot_token("discord", bot_id)
        dc_creds[bot_id] = token
        log.info("read token from /run/secrets/bot_token-%s", bot_id)

    # Read per-bot settings then close — don't hold config.db open
    # during the long-lived adapter lifecycle (short-lived reads, same pattern).
    agent_store = AgentStore(db_path=vault_dir / "config.db")
    await agent_store.connect()
    dc_bot_watch_channels: dict[str, frozenset[int]] = {}
    try:
        for bot_cfg in dc_multi_cfg.bots:
            bot_settings = agent_store.get_bot_settings(
                "discord", bot_cfg.bot_id
            )
            raw_ids = bot_settings.get("watch_channels", [])
            valid: list[int] = []
            for ch in raw_ids:
                try:
                    valid.append(int(ch))
                except (ValueError, TypeError):
                    log.warning(
                        "watch_channels: invalid channel id %r for bot %r"
                        " — skipping",
                        ch,
                        bot_cfg.bot_id,
                    )
            dc_bot_watch_channels[bot_cfg.bot_id] = frozenset(valid)
    finally:
        await agent_store.close()

    return dc_multi_cfg, dc_creds, dc_bot_watch_channels


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
    """Close all wired Discord adapters, buses, and typing listeners."""
    close_coros = [
        coro
        for a, _, ibus, tl in wired_dc
        for coro in (a.close(), ibus.stop(), tl.stop())
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
        for a, tok, _, _ in wired_dc
    ]
    try:
        await stop_dc.wait()
        await close_safely(
            "dc-adapters", *[a.close() for a, _, _, _ in wired_dc]
        )
        for t in start_tasks:
            t.cancel()
        await asyncio.gather(*start_tasks, return_exceptions=True)
    finally:
        dc_bus_coros = [ibus.stop() for _, _, ibus, _ in wired_dc]
        await close_safely("dc-buses", *dc_bus_coros)
        await close_safely(
            "dc-typing", *[tl.stop() for _, _, _, tl in wired_dc]
        )
        await dc_thread_store.close()
        await dc_turn_store.close()


async def bootstrap_discord_standalone(
    nc: Any,
    raw_config: dict,
    config_bundle: AdapterConfigBundle,
    vault_dir: Path,
    platform_enum: Platform,
    *,
    _stop: asyncio.Event | None = None,
) -> None:
    """Bootstrap a standalone Discord adapter process connected to NATS."""
    dc_multi_cfg, dc_creds, dc_bot_watch_channels = await _bootstrap_discord_setup(
        raw_config, vault_dir
    )
    dc_thread_store, dc_turn_store = await _create_dc_stores(vault_dir)

    wired_dc: list[tuple] = []  # (DiscordAdapter, str, Bus, TypingListener)

    async def _wire_bot(bot_cfg: Any, token: str) -> tuple:
        """Wire a single Discord bot with NATS transport and typing listener."""
        bot_id = bot_cfg.bot_id

        from lyra.adapters.discord import DiscordAdapter
        from lyra.adapters.discord.adapter import _discord_scope_resolver
        from lyra.adapters.discord.discord_outbound import _discord_typing_worker
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

        adapter_dc = DiscordAdapter(
            bot_id=bot_id,
            inbound_bus=inbound_bus_dc,
            auto_thread=bot_cfg.auto_thread,
            thread_hot_hours=bot_cfg.thread_hot_hours,
            thread_store=dc_thread_store,
            watch_channels=dc_bot_watch_channels.get(bot_id, frozenset()),
            turn_store=dc_turn_store,
            tool_display_config=config_bundle.tool_display,
        )

        listener_dc = NatsOutboundListener(
            nc,
            platform_enum,
            bot_id,
            adapter_dc,
            queue_group=adapter_outbound(platform_enum.value, bot_id),
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
        await dc_typing_listener.start()

        return (adapter_dc, token, inbound_bus_dc, dc_typing_listener)

    for bot_cfg in dc_multi_cfg.bots:
        bot_id = bot_cfg.bot_id
        if bot_id not in dc_creds:
            continue
        token = dc_creds[bot_id]

        try:
            wired = await _wire_bot(bot_cfg, token)
        except Exception:
            await _close_dc_wired("dc-wired", wired_dc)
            await dc_thread_store.close()
            await dc_turn_store.close()
            raise

        wired_dc.append(wired)
        log.info(
            "adapter_standalone: Discord bot_id=%s ready (NATS mode)",
            bot_id,
        )

    if not wired_dc:
        sys.exit("No Discord adapters started — check credentials")
    await wait_for_hub(nc)
    stop_dc = setup_shutdown_event(_stop)
    await _bootstrap_discord_teardown(wired_dc, dc_thread_store, dc_turn_store, stop_dc)
