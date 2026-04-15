"""Standalone adapter bootstrap — NATS-connected adapter without local Hub."""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from lyra.adapters.nats_outbound_listener import NatsOutboundListener
from lyra.core.bus import Bus
from lyra.core.message import InboundMessage, Platform
from lyra.core.stores.credential_store import CredentialStore, LyraKeyring
from lyra.nats import nats_connect
from lyra.nats.connect import scrub_nats_url
from lyra.nats.queue_groups import adapter_outbound
from lyra.nats.readiness import wait_for_hub

log = logging.getLogger(__name__)


async def _bootstrap_adapter_standalone(  # noqa: PLR0915, C901
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

    try:
        nc = await nats_connect(nats_url)
        log.info(
            "adapter_standalone: connected to NATS at %s",
            scrub_nats_url(nats_url),
        )
    except Exception as exc:
        sys.exit(f"Failed to connect to NATS at {scrub_nats_url(nats_url)!r}: {exc}")

    platform_enum = Platform(platform)

    from lyra.nats.nats_bus import NatsBus

    vault_dir = Path(os.environ.get("LYRA_VAULT_DIR", str(Path.home() / ".lyra")))
    vault_dir.mkdir(parents=True, exist_ok=True)

    try:
        if platform == "telegram":
            from lyra.adapters.telegram import TelegramAdapter
            from lyra.config import TelegramMultiConfig

            tg_multi_cfg = TelegramMultiConfig.model_validate(
                raw_config.get("telegram", {})
            )
            if not tg_multi_cfg.bots:
                sys.exit("No telegram bots configured")

            # Gather credentials then close the store immediately — don't hold
            # config.db open during long-lived polling (causes Hub DB lock).
            keyring = LyraKeyring.load_or_create(vault_dir / "keyring.key")
            cred_store = CredentialStore(
                db_path=vault_dir / "config.db", keyring=keyring
            )
            await cred_store.connect()
            tg_creds: dict[str, tuple[str, str | None]] = {}
            try:
                for bot_cfg in tg_multi_cfg.bots:
                    bot_id = bot_cfg.bot_id
                    creds = await cred_store.get_full("telegram", bot_id)
                    if creds is None:
                        log.error(
                            "adapter_standalone: no credentials for telegram/%s"
                            " — skipping",
                            bot_id,
                        )
                        continue
                    tg_creds[bot_id] = creds
            finally:
                await cred_store.close()

            from lyra.core.stores.turn_store import TurnStore as TurnStore

            tg_turn_store = TurnStore(db_path=vault_dir / "turns.db")
            await tg_turn_store.connect()

            wired: list[tuple] = []  # (TelegramAdapter, Bus)

            for bot_cfg in tg_multi_cfg.bots:
                bot_id = bot_cfg.bot_id
                if bot_id not in tg_creds:
                    continue
                token, webhook_secret = tg_creds[bot_id]

                inbound_bus: Bus[InboundMessage] = NatsBus(  # type: ignore[type-arg]
                    nc=nc, bot_id=bot_id, item_type=InboundMessage, publish_only=True,
                )
                inbound_bus.register(platform_enum)
                await inbound_bus.start()

                adapter = TelegramAdapter(
                    bot_id=bot_id,
                    token=token,
                    inbound_bus=inbound_bus,
                    webhook_secret=webhook_secret or "",
                    turn_store=tg_turn_store,
                )
                await adapter.resolve_identity()

                listener = NatsOutboundListener(
                    nc, platform_enum, bot_id, adapter,
                    queue_group=adapter_outbound(platform_enum.value, bot_id),
                )
                adapter._outbound_listener = listener
                await adapter.astart()

                wired.append((adapter, inbound_bus))
                log.info(
                    "adapter_standalone: Telegram bot_id=%s ready (NATS mode)", bot_id
                )

            if not wired:
                sys.exit("No Telegram adapters started — check credentials")
            await wait_for_hub(nc)

            stop = _stop if _stop is not None else asyncio.Event()
            if _stop is None:
                import signal
                loop = asyncio.get_running_loop()
                for sig in (signal.SIGTERM, signal.SIGINT):
                    loop.add_signal_handler(sig, stop.set)

            poll_tasks = [
                asyncio.create_task(
                    a.dp.start_polling(a.bot, handle_signals=False),
                    name=f"telegram:{a._bot_id}",
                )
                for a, _ in wired
            ]
            try:
                await stop.wait()
                for a, _ in wired:
                    await a.dp.stop_polling()
                await asyncio.gather(*poll_tasks, return_exceptions=True)
            finally:
                for a, ibus in wired:
                    await a.close()
                    await ibus.stop()
                await tg_turn_store.close()

        elif platform == "discord":
            from lyra.adapters.discord import DiscordAdapter
            from lyra.config import DiscordMultiConfig

            dc_multi_cfg = DiscordMultiConfig.model_validate(
                raw_config.get("discord", {})
            )
            if not dc_multi_cfg.bots:
                sys.exit("No discord bots configured")

            # Gather credentials then close the store immediately.
            keyring = LyraKeyring.load_or_create(vault_dir / "keyring.key")
            cred_store = CredentialStore(
                db_path=vault_dir / "config.db", keyring=keyring
            )
            await cred_store.connect()
            dc_creds: dict[str, str] = {}
            try:
                for bot_cfg in dc_multi_cfg.bots:
                    bot_id = bot_cfg.bot_id
                    creds = await cred_store.get_full("discord", bot_id)
                    if creds is None:
                        log.error(
                            "adapter_standalone: no credentials for discord/%s"
                            " — skipping",
                            bot_id,
                        )
                        continue
                    token, _ = creds
                    dc_creds[bot_id] = token
            finally:
                await cred_store.close()

            from lyra.core.stores.agent_store import AgentStore
            from lyra.core.stores.thread_store import ThreadStore

            # Read per-bot settings then close — don't hold config.db open
            # during the long-lived adapter lifecycle (same pattern as CredentialStore).
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

            dc_thread_store = ThreadStore(db_path=vault_dir / "discord.db")
            await dc_thread_store.connect()

            from lyra.core.stores.turn_store import TurnStore

            dc_turn_store = TurnStore(db_path=vault_dir / "turns.db")
            await dc_turn_store.connect()

            wired_dc: list[tuple] = []  # (DiscordAdapter, str, Bus)

            for bot_cfg in dc_multi_cfg.bots:
                bot_id = bot_cfg.bot_id
                if bot_id not in dc_creds:
                    continue
                token = dc_creds[bot_id]

                inbound_bus_dc: Bus[InboundMessage] = NatsBus(  # type: ignore[type-arg]
                    nc=nc, bot_id=bot_id, item_type=InboundMessage, publish_only=True,
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
                )

                listener_dc = NatsOutboundListener(
                    nc, platform_enum, bot_id, adapter_dc,
                    queue_group=adapter_outbound(platform_enum.value, bot_id),
                )
                adapter_dc._outbound_listener = listener_dc
                await adapter_dc.astart()

                wired_dc.append((adapter_dc, token, inbound_bus_dc))
                log.info(
                    "adapter_standalone: Discord bot_id=%s ready (NATS mode)", bot_id
                )

            if not wired_dc:
                sys.exit("No Discord adapters started — check credentials")
            await wait_for_hub(nc)

            stop_dc = _stop if _stop is not None else asyncio.Event()
            if _stop is None:
                import signal
                loop = asyncio.get_running_loop()
                for sig in (signal.SIGTERM, signal.SIGINT):
                    loop.add_signal_handler(sig, stop_dc.set)

            start_tasks = [
                asyncio.create_task(
                    a.start(tok),
                    name=f"discord:{a._bot_id}",
                )
                for a, tok, _ in wired_dc
            ]
            try:
                await stop_dc.wait()
                for a, _, _ in wired_dc:
                    await a.close()
                await asyncio.gather(*start_tasks, return_exceptions=True)
            finally:
                for _, _, ibus in wired_dc:
                    await ibus.stop()
                await dc_turn_store.close()

        else:
            sys.exit(f"Unknown platform: {platform!r}")
    finally:
        await nc.close()
