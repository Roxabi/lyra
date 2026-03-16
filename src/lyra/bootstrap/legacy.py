"""Bootstrap legacy — hub wiring for legacy single-bot configuration schema."""

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
    _build_provider_registry,
    _create_agent,
)
from lyra.bootstrap.config import (
    _build_agent_overrides,
    _load_messages,
    _load_pairing_config,
)
from lyra.bootstrap.health import create_health_app
from lyra.core.agent import load_agent_config
from lyra.core.auth import AuthMiddleware
from lyra.core.auth_store import AuthStore
from lyra.core.circuit_breaker import CircuitRegistry
from lyra.core.cli_pool import CliPool
from lyra.core.credential_store import CredentialStore, LyraKeyring
from lyra.core.hub import Hub, RoutingKey
from lyra.core.message import Platform
from lyra.core.outbound_dispatcher import OutboundDispatcher
from lyra.core.pairing import PairingManager, set_pairing_manager
from lyra.core.thread_store import ThreadStore
from lyra.core.turn_store import TurnStore
from lyra.errors import MissingCredentialsError
from lyra.stt import STTService, load_stt_config
from lyra.tts import TTSService, load_tts_config

log = logging.getLogger(__name__)


async def _bootstrap_legacy(  # noqa: C901, PLR0915 — startup wiring: each adapter/service requires sequential conditional setup
    raw_config: dict,
    circuit_registry: CircuitRegistry,
    admin_user_ids: set[str],
    *,
    adapter: str = "all",
    _stop: asyncio.Event | None = None,
) -> None:
    """Wire hub + adapters for the legacy single-bot schema and run until stop.

    Handles [auth.telegram] / [auth.discord] flat sections from config.toml.
    Behavior is identical to the original single-bot implementation.
    """
    # ------------------------------------------------------------------
    # Legacy single-bot path (backward compat).
    # Runs when no [[telegram.bots]] / [[discord.bots]] arrays are found.
    # Behavior is identical to the original single-bot implementation.
    # ------------------------------------------------------------------
    vault_dir_lg = Path(os.environ.get("LYRA_VAULT_DIR", str(Path.home() / ".lyra")))
    vault_dir_lg.mkdir(parents=True, exist_ok=True)
    auth_store_legacy = AuthStore(db_path=vault_dir_lg / "auth.db")
    await auth_store_legacy.connect()
    keyring_lg = LyraKeyring.load_or_create(vault_dir_lg / "keyring.key")
    cred_store_legacy = CredentialStore(
        db_path=vault_dir_lg / "auth.db",
        keyring=keyring_lg,
    )
    await cred_store_legacy.connect()
    turn_store_lg = TurnStore(db_path=vault_dir_lg / "turns.db")
    await turn_store_lg.connect()
    thread_store_lg = ThreadStore(db_path=vault_dir_lg / "auth.db")
    await thread_store_lg.connect()
    await auth_store_legacy.seed_from_config(raw_config, "telegram")
    await auth_store_legacy.seed_from_config(raw_config, "discord")

    try:
        tg_auth = (
            None
            if adapter == "discord"
            else AuthMiddleware.from_config(
                raw_config, "telegram", store=auth_store_legacy
            )
        )
        dc_auth = (
            None
            if adapter == "telegram"
            else AuthMiddleware.from_config(
                raw_config, "discord", store=auth_store_legacy
            )
        )
    except ValueError as exc:
        sys.exit(str(exc))

    if tg_auth is None and dc_auth is None:
        sys.exit(
            "No adapters configured — add at least one of [auth.telegram] or"
            " [auth.discord] to config.toml"
        )

    # Resolve credentials from CredentialStore (bot_id="main" for legacy path).
    # Non-credential config (bot_username, auto_thread) is read from env vars.
    tg_token_lg: str | None = None
    tg_webhook_secret_lg: str | None = None
    tg_bot_username_lg: str = os.environ.get("TELEGRAM_BOT_USERNAME", "lyra_bot")
    if tg_auth is not None:
        tg_creds_lg = await cred_store_legacy.get_full("telegram", "main")
        if tg_creds_lg is None:
            raise MissingCredentialsError("telegram", "main")
        tg_token_lg, tg_webhook_secret_lg = tg_creds_lg

    dc_token_lg: str | None = None
    dc_auto_thread_lg: bool = True
    if dc_auth is not None:
        dc_creds_lg = await cred_store_legacy.get_full("discord", "main")
        if dc_creds_lg is None:
            raise MissingCredentialsError("discord", "main")
        dc_token_lg, _ = dc_creds_lg
        auto_thread_str_lg = os.environ.get("DISCORD_AUTO_THREAD", "").strip().lower()
        dc_auto_thread_lg = (
            auto_thread_str_lg in {"1", "true", "yes", "on"}
            if auto_thread_str_lg
            else True
        )

    agent_config = load_agent_config(
        "lyra_default",
        instance_overrides=_build_agent_overrides(raw_config, "lyra_default"),
    )
    log.info(
        "Agent loaded: name=%s model=%s backend=%s",
        agent_config.name,
        agent_config.model_config.model,
        agent_config.model_config.backend,
    )

    msg_manager = _load_messages(language=agent_config.i18n_language)

    pairing_config = _load_pairing_config(raw_config)
    if pairing_config.enabled and not admin_user_ids:
        log.warning(
            "Pairing is enabled but no admin user_ids configured in "
            "[admin].user_ids — no one can run /invite or /unpair"
        )
    pm_legacy: PairingManager | None = None
    if pairing_config.enabled:
        pm_legacy = PairingManager(
            config=pairing_config,
            db_path=vault_dir_lg / "pairing.db",
            admin_user_ids=admin_user_ids,
            auth_store=auth_store_legacy,
        )
        await pm_legacy.connect()
        set_pairing_manager(pm_legacy)

    stt_service_legacy: STTService | None = None
    if os.environ.get("STT_MODEL_SIZE"):
        try:
            stt_cfg = load_stt_config()
            stt_service_legacy = STTService(stt_cfg)
            log.info("STT enabled: model=%s (via voiceCLI)", stt_cfg.model_size)
        except ValueError as exc:
            raise SystemExit(f"Invalid STT configuration: {exc}") from exc

    tts_service_legacy: TTSService | None = None
    _voice_responses_on = os.environ.get("LYRA_VOICE_RESPONSES", "1") != "0"
    if stt_service_legacy is not None and _voice_responses_on:
        tts_cfg = load_tts_config()
        tts_service_legacy = TTSService(tts_cfg)
        log.info(
            "TTS voice responses enabled: engine=%s voice=%s",
            tts_cfg.engine or "default",
            tts_cfg.voice or "default",
        )

    from lyra.core.debouncer import DEFAULT_DEBOUNCE_MS

    hub = Hub(
        circuit_registry=circuit_registry,
        msg_manager=msg_manager,
        pairing_manager=pm_legacy,
        stt=stt_service_legacy,
        tts=tts_service_legacy,
        debounce_ms=DEFAULT_DEBOUNCE_MS,
    )
    hub.set_turn_store(turn_store_lg)

    cli_pool_legacy: CliPool | None = None
    if agent_config.model_config.backend == "claude-cli":
        cli_pool_legacy = CliPool()
        await cli_pool_legacy.start()

    sr_config = agent_config.smart_routing
    if (
        sr_config is not None
        and sr_config.enabled
        and agent_config.model_config.backend != "anthropic-sdk"
    ):
        log.warning(
            "smart_routing.enabled=true but backend=%r — "
            "smart routing only applies to anthropic-sdk",
            agent_config.model_config.backend,
        )
    provider_registry, smart_routing_decorator = _build_provider_registry(
        circuit_registry, cli_pool_legacy, smart_routing_config=sr_config
    )

    agent = _create_agent(
        agent_config,
        cli_pool_legacy,
        circuit_registry=circuit_registry,
        admin_user_ids=admin_user_ids,
        msg_manager=msg_manager,
        stt=stt_service_legacy,
        tts=tts_service_legacy,
        provider_registry=provider_registry,
        smart_routing_decorator=smart_routing_decorator,
    )
    hub.register_agent(agent)

    tg_adapter: TelegramAdapter | None = None
    dc_adapter: DiscordAdapter | None = None

    if tg_auth is not None and tg_token_lg is not None:
        tg_adapter = TelegramAdapter(
            bot_id="main",
            token=tg_token_lg,
            hub=hub,
            bot_username=tg_bot_username_lg,
            webhook_secret=tg_webhook_secret_lg or "",
            circuit_registry=circuit_registry,
            msg_manager=msg_manager,
            auth=tg_auth,
        )
        hub.register_adapter(Platform.TELEGRAM, "main", tg_adapter)
        tg_key = RoutingKey(Platform.TELEGRAM, "main", "*")
        hub.register_binding(
            Platform.TELEGRAM, "main", "*", agent.name, tg_key.to_pool_id()
        )

    if dc_auth is not None and dc_token_lg is not None:
        dc_adapter = DiscordAdapter(
            hub=hub,
            bot_id="main",
            circuit_registry=circuit_registry,
            msg_manager=msg_manager,
            auto_thread=dc_auto_thread_lg,
            auth=dc_auth,
            thread_store=thread_store_lg,
        )
        hub.register_adapter(Platform.DISCORD, "main", dc_adapter)
        dc_key = RoutingKey(Platform.DISCORD, "main", "*")
        hub.register_binding(
            Platform.DISCORD, "main", "*", agent.name, dc_key.to_pool_id()
        )

    # Create and register per-platform outbound dispatchers
    tg_dispatcher_legacy: OutboundDispatcher | None = None
    dc_dispatcher_legacy: OutboundDispatcher | None = None

    if tg_adapter is not None:
        tg_dispatcher_legacy = OutboundDispatcher(
            platform_name="telegram",
            adapter=tg_adapter,
            circuit=circuit_registry.get("telegram"),
            circuit_registry=circuit_registry,
            bot_id="main",
        )
        hub.register_outbound_dispatcher(
            Platform.TELEGRAM, "main", tg_dispatcher_legacy
        )

    if dc_adapter is not None:
        dc_dispatcher_legacy = OutboundDispatcher(
            platform_name="discord",
            adapter=dc_adapter,
            circuit=circuit_registry.get("discord"),
            circuit_registry=circuit_registry,
            bot_id="main",
        )
        hub.register_outbound_dispatcher(Platform.DISCORD, "main", dc_dispatcher_legacy)

    # Start InboundBus feeder tasks and OutboundDispatcher workers
    await hub.inbound_bus.start()
    await hub.inbound_audio_bus.start()
    if tg_dispatcher_legacy is not None:
        await tg_dispatcher_legacy.start()
    if dc_dispatcher_legacy is not None:
        await dc_dispatcher_legacy.start()

    # Health endpoint (SC-1): root FastAPI app on configurable port
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
    if tg_adapter is not None:
        tasks.append(
            asyncio.create_task(
                # handle_signals=False is mandatory when aiogram runs inside
                # asyncio.gather() — otherwise it installs its own signal handlers
                # that conflict with the event loop's, causing double-cancellation.
                tg_adapter.dp.start_polling(tg_adapter.bot, handle_signals=False),
                name="telegram",
            )
        )
    if dc_adapter is not None and dc_token_lg is not None:
        _dc_task_lg = asyncio.create_task(dc_adapter.start(dc_token_lg), name="discord")
        _dc_task_lg.add_done_callback(_log_task_failure)
        tasks.append(_dc_task_lg)

    task_names = {t.get_name() for t in tasks}
    active = [name for name in ("telegram", "discord") if name in task_names]
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
    if tg_dispatcher_legacy is not None:
        await tg_dispatcher_legacy.stop()
    if dc_dispatcher_legacy is not None:
        await dc_dispatcher_legacy.stop()
    if dc_adapter is not None:
        await dc_adapter.close()
    if pm_legacy is not None:
        await pm_legacy.close()
    await cred_store_legacy.close()
    await auth_store_legacy.close()
    await turn_store_lg.close()
    await thread_store_lg.close()
    if cli_pool_legacy is not None:
        await cli_pool_legacy.stop()
    log.info("Lyra stopped.")
