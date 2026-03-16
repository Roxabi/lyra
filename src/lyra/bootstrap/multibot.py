"""Bootstrap multibot — hub wiring for multi-bot configuration schema."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

import uvicorn

from lyra.adapters.discord import DiscordAdapter
from lyra.adapters.telegram import TelegramAdapter
from lyra.bootstrap.agent_factory import (
    _resolve_agents,
    _resolve_bot_agent_map,
    apply_agent_stt_overlay,
    apply_agent_tts_overlay,
)
from lyra.bootstrap.config import (
    _build_agent_overrides,
    _load_messages,
    _load_pairing_config,
)
from lyra.bootstrap.health import create_health_app
from lyra.config import (
    DiscordBotConfig,
    DiscordMultiConfig,
    TelegramBotConfig,
    TelegramMultiConfig,
)
from lyra.core.agent import agent_row_to_config, load_agent_config
from lyra.core.agent_store import AgentStore
from lyra.core.auth import AuthMiddleware
from lyra.core.auth_store import AuthStore
from lyra.core.circuit_breaker import CircuitRegistry
from lyra.core.cli_pool import CliPool
from lyra.core.credential_store import CredentialStore, LyraKeyring
from lyra.core.hub import Hub, RoutingKey
from lyra.core.message import Platform
from lyra.core.outbound_dispatcher import OutboundDispatcher
from lyra.core.pairing import PairingManager, set_pairing_manager
from lyra.core.prefs_store import PrefsStore
from lyra.core.thread_store import ThreadStore
from lyra.core.turn_store import TurnStore
from lyra.errors import MissingCredentialsError
from lyra.stt import STTService, load_stt_config
from lyra.tts import TTSService, load_tts_config

log = logging.getLogger(__name__)


async def _bootstrap_multibot(  # noqa: C901, PLR0915 — startup wiring: each adapter/service requires sequential conditional setup
    raw_config: dict,
    circuit_registry: CircuitRegistry,
    admin_user_ids: set[str],
    tg_multi_cfg: TelegramMultiConfig,
    dc_multi_cfg: DiscordMultiConfig,
    *,
    _stop: asyncio.Event | None = None,
) -> None:
    """Wire hub + adapters for the multi-bot configuration schema and run until stop.

    Handles [[telegram.bots]] / [[discord.bots]] arrays from config.toml.
    Each bot is wired individually with its own AuthMiddleware, OutboundDispatcher,
    and adapter.
    """
    # ------------------------------------------------------------------
    # Multi-bot path: validate auth per bot, collect unique agent names.
    # ------------------------------------------------------------------
    vault_dir_mb = Path(os.environ.get("LYRA_VAULT_DIR", str(Path.home() / ".lyra")))
    vault_dir_mb.mkdir(parents=True, exist_ok=True)
    auth_store: AuthStore | None = None
    cred_store: CredentialStore | None = None
    agent_store: AgentStore | None = None
    turn_store_mb: TurnStore | None = None
    thread_store_mb: ThreadStore | None = None
    prefs_store: PrefsStore | None = None
    try:
        auth_store = AuthStore(db_path=vault_dir_mb / "auth.db")
        await auth_store.connect()
        keyring_mb = LyraKeyring.load_or_create(vault_dir_mb / "keyring.key")
        cred_store = CredentialStore(
            db_path=vault_dir_mb / "auth.db",
            keyring=keyring_mb,
        )
        await cred_store.connect()
        agent_store = AgentStore(db_path=vault_dir_mb / "auth.db")
        await agent_store.connect()
        turn_store_mb = TurnStore(db_path=vault_dir_mb / "turns.db")
        await turn_store_mb.connect()
        thread_store_mb = ThreadStore(db_path=vault_dir_mb / "auth.db")
        await thread_store_mb.connect()
        # Seed per-bot owner/trusted users as permanent grants
        auth_block: dict = raw_config.get("auth", {})
        for entry in auth_block.get("telegram_bots", []):
            synthetic = {"auth": {"telegram": entry}}
            await auth_store.seed_from_config(synthetic, "telegram")
        for entry in auth_block.get("discord_bots", []):
            synthetic = {"auth": {"discord": entry}}
            await auth_store.seed_from_config(synthetic, "discord")

        tg_bot_auths: list[tuple[TelegramBotConfig, AuthMiddleware]] = []
        dc_bot_auths: list[tuple[DiscordBotConfig, AuthMiddleware]] = []

        try:
            for bot_cfg in tg_multi_cfg.bots:
                auth = AuthMiddleware.from_bot_config(
                    raw_config, "telegram", bot_cfg.bot_id, store=auth_store
                )
                if auth is None:
                    log.warning(
                        "telegram bot_id=%r has no auth config — skipping",
                        bot_cfg.bot_id,
                    )
                    continue
                tg_bot_auths.append((bot_cfg, auth))

            for bot_cfg in dc_multi_cfg.bots:
                auth = AuthMiddleware.from_bot_config(
                    raw_config, "discord", bot_cfg.bot_id, store=auth_store
                )
                if auth is None:
                    log.warning(
                        "discord bot_id=%r has no auth config — skipping",
                        bot_cfg.bot_id,
                    )
                    continue
                dc_bot_auths.append((bot_cfg, auth))
        except ValueError as exc:
            sys.exit(str(exc))

        if not tg_bot_auths and not dc_bot_auths:
            sys.exit(
                "No adapters configured — add at least one [[telegram.bots]] or"
                " [[discord.bots]] entry with a matching [[auth.telegram_bots]] or"
                " [[auth.discord_bots]] section to config.toml"
            )

        # Resolve (platform, bot_id) → agent_name: DB cache first, TOML fallback + seed.
        bot_agent_map = await _resolve_bot_agent_map(
            agent_store, tg_multi_cfg.bots, dc_multi_cfg.bots
        )
        agent_names: set[str] = set(bot_agent_map.values())

        # Load all agent configs once (DB-first, TOML fallback for pre-migration
        # compat). Used for cli_pool detection, smart routing, msg_manager, creation.
        from lyra.core.agent import Agent

        agent_configs: dict[str, Agent] = {}
        for n in sorted(agent_names):
            row = agent_store.get(n)
            if row is not None:
                agent_configs[n] = agent_row_to_config(
                    row, instance_overrides=_build_agent_overrides(raw_config, n)
                )
            else:
                # Agent not in DB — fall back to TOML file (pre-migration compatibility)
                try:
                    agent_configs[n] = load_agent_config(
                        n, instance_overrides=_build_agent_overrides(raw_config, n)
                    )
                except Exception as exc:  # noqa: BLE001
                    log.error("Failed to load agent %r: %s — skipping", n, exc)
        if not agent_configs:
            sys.exit(
                "No agent configs could be loaded — run 'lyra agent init' to seed the"
                " agents table, or ensure agents/*.toml files exist"
            )
        first_agent_name = next(iter(sorted(agent_configs)))
        first_agent_config = agent_configs[first_agent_name]

        # Use the first agent's language for messages (all agents share one msg_manager)
        msg_manager = _load_messages(language=first_agent_config.i18n_language)

        pairing_config = _load_pairing_config(raw_config)
        if pairing_config.enabled and not admin_user_ids:
            log.warning(
                "Pairing is enabled but no admin user_ids configured in "
                "[admin].user_ids — no one can run /invite or /unpair"
            )
        pm: PairingManager | None = None
        if pairing_config.enabled:
            pm = PairingManager(
                config=pairing_config,
                db_path=vault_dir_mb / "pairing.db",
                admin_user_ids=admin_user_ids,
                auth_store=auth_store,
            )
            await pm.connect()
            set_pairing_manager(pm)

        stt_service: STTService | None = None
        if os.environ.get("STT_MODEL_SIZE"):
            try:
                # Env vars set baseline; agent TOML overlays on top
                stt_cfg = apply_agent_stt_overlay(
                    first_agent_config.stt, load_stt_config()
                )
                stt_service = STTService(stt_cfg)
                log.info("STT enabled: model=%s (via voiceCLI)", stt_cfg.model_size)
            except ValueError as exc:
                raise SystemExit(f"Invalid STT configuration: {exc}") from exc

        tts_service: TTSService | None = None
        voice_responses = os.environ.get("LYRA_VOICE_RESPONSES", "1") != "0"
        if stt_service is not None and voice_responses:
            # Env vars set baseline; agent TOML overlays on top (first alphabetically)
            tts_cfg = apply_agent_tts_overlay(first_agent_config.tts, load_tts_config())
            # Warn if multiple agents define conflicting [tts] sections
            _agents_with_tts = [
                n for n, cfg in agent_configs.items() if cfg.tts is not None
            ]
            if len(_agents_with_tts) > 1:
                log.warning(
                    "Multiple agents define [tts] config: %s — "
                    "using %r (first alphabetically). "
                    "Other agents' [tts] settings are ignored.",
                    _agents_with_tts,
                    first_agent_name,
                )
            tts_service = TTSService(tts_cfg)
            log.info(
                "TTS voice responses enabled: engine=%s voice=%s",
                tts_cfg.engine or "default",
                tts_cfg.voice or "default",
            )

        from lyra.core.debouncer import DEFAULT_DEBOUNCE_MS

        prefs_store = PrefsStore(db_path=vault_dir_mb / "auth.db")
        await prefs_store.connect()

        hub = Hub(
            circuit_registry=circuit_registry,
            msg_manager=msg_manager,
            pairing_manager=pm,
            stt=stt_service,
            tts=tts_service,
            debounce_ms=DEFAULT_DEBOUNCE_MS,
            prefs_store=prefs_store,
        )
        hub.set_turn_store(turn_store_mb)

        # Build cli_pool if any agent needs it (uses pre-loaded configs)
        cli_pool: CliPool | None = None
        for cfg in agent_configs.values():
            if cfg.model_config.backend == "claude-cli":
                cli_pool = CliPool()
                await cli_pool.start()
                break

        # Create all unique agents — each gets its own ProviderRegistry built from
        # its own smart_routing config inside _resolve_agents().
        all_agents = _resolve_agents(
            agent_configs,
            cli_pool,
            circuit_registry,
            admin_user_ids,
            msg_manager,
            stt_service,
            tts_service,
        )
        for ag in all_agents.values():
            hub.register_agent(ag)

        # Wire Telegram adapters
        tg_adapters: list[TelegramAdapter] = []
        tg_dispatchers: list[OutboundDispatcher] = []
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
                bot_username=bot_cfg.bot_username,
                webhook_secret=tg_webhook_secret or "",
                circuit_registry=circuit_registry,
                msg_manager=msg_manager,
                auth=auth,
            )
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
            hub.register_outbound_dispatcher(
                Platform.TELEGRAM, bot_cfg.bot_id, dispatcher
            )
            tg_adapters.append(adapter)
            tg_dispatchers.append(dispatcher)
            log.info(
                "Registered Telegram bot bot_id=%r agent=%r",
                bot_cfg.bot_id,
                resolved_agent,
            )

        # Wire Discord adapters
        dc_adapters: list[tuple[DiscordAdapter, DiscordBotConfig, str]] = []
        dc_dispatchers: list[OutboundDispatcher] = []
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
            adapter = DiscordAdapter(
                hub=hub,
                bot_id=bot_cfg.bot_id,
                circuit_registry=circuit_registry,
                msg_manager=msg_manager,
                auto_thread=bot_cfg.auto_thread,
                auth=auth,
                thread_store=thread_store_mb,
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
            hub.register_outbound_dispatcher(
                Platform.DISCORD, bot_cfg.bot_id, dispatcher
            )
            dc_adapters.append((adapter, bot_cfg, dc_token))
            dc_dispatchers.append(dispatcher)
            log.info(
                "Registered Discord bot bot_id=%r agent=%r",
                bot_cfg.bot_id,
                resolved_agent,
            )

        # Start buses and dispatchers
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
            asyncio.create_task(hub._audio_loop(), name="hub-audio"),
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

        log.info("Shutdown signal received — stopping…")
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
            await cli_pool.stop()
        log.info("Lyra stopped.")
    finally:
        if cred_store is not None:
            await cred_store.close()
        if auth_store is not None:
            await auth_store.close()
        if agent_store is not None:
            await agent_store.close()
        if turn_store_mb is not None:
            await turn_store_mb.close()
        if thread_store_mb is not None:
            await thread_store_mb.close()
        if prefs_store is not None:
            await prefs_store.close()
