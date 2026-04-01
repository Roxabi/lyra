"""Standalone adapter bootstrap — runs TelegramAdapter or DiscordAdapter as a pure
NATS client, without a local Hub instance.

Entry point: _bootstrap_adapter_standalone(raw_config, platform)

Used by lyra_telegram and lyra_discord supervisor programs in NATS mode (ADR-037).
Tokens are read from the encrypted credential store (same source as hub mode).
Telegram uses aiogram long-polling; Discord uses its websocket connection.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

import nats

from lyra.adapters.nats_outbound_listener import NatsOutboundListener
from lyra.core.bus import Bus
from lyra.core.inbound_bus import LocalBus
from lyra.core.message import InboundAudio, InboundMessage, Platform
from lyra.core.stores.credential_store import CredentialStore, LyraKeyring

log = logging.getLogger(__name__)


async def _bootstrap_adapter_standalone(  # noqa: PLR0915
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

    vault_dir = Path(os.environ.get("LYRA_VAULT_DIR", str(Path.home() / ".lyra")))
    vault_dir.mkdir(parents=True, exist_ok=True)

    # Adapters only need credentials — open CredentialStore directly rather than
    # the full open_stores() bundle. This avoids SQLite contention with the Hub
    # process which opens all stores simultaneously at startup.
    keyring = LyraKeyring.load_or_create(vault_dir / "keyring.key")
    cred_store = CredentialStore(db_path=vault_dir / "auth.db", keyring=keyring)
    await cred_store.connect()

    # nc.close() is deferred to the outer finally so the shared connection stays
    # alive for all bots (previously closed per-iteration — bug).
    try:
        if platform == "telegram":
            from lyra.adapters.telegram import TelegramAdapter
            from lyra.config import TelegramMultiConfig

            tg_multi_cfg = TelegramMultiConfig.model_validate(
                raw_config.get("telegram", {})
            )
            if not tg_multi_cfg.bots:
                sys.exit("No telegram bots configured")

            # Wire all bots; collect (adapter, inbound_bus) pairs for teardown
            wired: list[tuple[TelegramAdapter, Bus[InboundMessage]]] = []

            for bot_cfg in tg_multi_cfg.bots:
                bot_id = bot_cfg.bot_id

                tg_creds = await cred_store.get_full("telegram", bot_id)
                if tg_creds is None:
                    log.error(
                        "adapter_standalone: no credentials for telegram/%s — skipping",
                        bot_id,
                    )
                    continue
                token, webhook_secret = tg_creds

                inbound_bus: Bus[InboundMessage] = NatsBus(  # type: ignore[type-arg]
                    nc=nc,
                    bot_id=bot_id,
                    item_type=InboundMessage,
                )
                inbound_bus.register(platform_enum)
                await inbound_bus.start()

                inbound_audio_bus: Bus[InboundAudio] = LocalBus(name="inbound-audio")  # type: ignore[type-arg]
                inbound_audio_bus.register(platform_enum)

                adapter = TelegramAdapter(
                    bot_id=bot_id,
                    token=token,
                    inbound_bus=inbound_bus,
                    inbound_audio_bus=inbound_audio_bus,
                    webhook_secret=webhook_secret or "",
                )
                await adapter.resolve_identity()

                listener = NatsOutboundListener(nc, platform_enum, bot_id, adapter)
                adapter._outbound_listener = listener
                await adapter.astart()

                wired.append((adapter, inbound_bus))
                log.info(
                    "adapter_standalone: Telegram bot_id=%s ready (NATS mode)", bot_id
                )

            if not wired:
                sys.exit("No Telegram adapters started — check credentials")

            stop = _stop if _stop is not None else asyncio.Event()
            if _stop is None:
                import signal
                loop = asyncio.get_running_loop()
                for sig in (signal.SIGTERM, signal.SIGINT):
                    loop.add_signal_handler(sig, stop.set)

            # All bots poll concurrently
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

        elif platform == "discord":
            from lyra.adapters.discord import DiscordAdapter
            from lyra.config import DiscordMultiConfig

            dc_multi_cfg = DiscordMultiConfig.model_validate(
                raw_config.get("discord", {})
            )
            if not dc_multi_cfg.bots:
                sys.exit("No discord bots configured")

            wired_dc: list[tuple[DiscordAdapter, str, Bus[InboundMessage]]] = []

            for bot_cfg in dc_multi_cfg.bots:
                bot_id = bot_cfg.bot_id

                dc_creds = await cred_store.get_full("discord", bot_id)
                if dc_creds is None:
                    log.error(
                        "adapter_standalone: no credentials for discord/%s — skipping",
                        bot_id,
                    )
                    continue
                token, _ = dc_creds

                inbound_bus_dc: Bus[InboundMessage] = NatsBus(  # type: ignore[type-arg]
                    nc=nc,
                    bot_id=bot_id,
                    item_type=InboundMessage,
                )
                inbound_bus_dc.register(platform_enum)
                await inbound_bus_dc.start()

                inbound_audio_bus_dc: Bus[InboundAudio] = LocalBus(name="inbound-audio")  # type: ignore[type-arg]
                inbound_audio_bus_dc.register(platform_enum)

                adapter_dc = DiscordAdapter(
                    bot_id=bot_id,
                    inbound_bus=inbound_bus_dc,
                    inbound_audio_bus=inbound_audio_bus_dc,
                    auto_thread=bot_cfg.auto_thread,
                    thread_hot_hours=bot_cfg.thread_hot_hours,
                )

                listener_dc = NatsOutboundListener(
                    nc, platform_enum, bot_id, adapter_dc
                )
                adapter_dc._outbound_listener = listener_dc
                await adapter_dc.astart()

                wired_dc.append((adapter_dc, token, inbound_bus_dc))
                log.info(
                    "adapter_standalone: Discord bot_id=%s ready (NATS mode)", bot_id
                )

            if not wired_dc:
                sys.exit("No Discord adapters started — check credentials")

            stop_dc = _stop if _stop is not None else asyncio.Event()
            if _stop is None:
                import signal
                loop = asyncio.get_running_loop()
                for sig in (signal.SIGTERM, signal.SIGINT):
                    loop.add_signal_handler(sig, stop_dc.set)

            # All bots connect concurrently
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
        else:
            sys.exit(f"Unknown platform: {platform!r}")
    finally:
        await cred_store.close()
        await nc.close()
