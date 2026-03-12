"""Entry point: python -m lyra.

Starts Telegram + Discord adapters in one event loop.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import signal
import time
import tomllib
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException

from lyra.adapters.discord import DiscordAdapter, load_discord_config
from lyra.adapters.telegram import TelegramAdapter
from lyra.adapters.telegram import load_config as load_telegram_config
from lyra.agents.simple_agent import SimpleAgent
from lyra.core.agent import Agent, AgentBase, load_agent_config
from lyra.core.circuit_breaker import CircuitBreaker, CircuitRegistry
from lyra.core.cli_pool import CliPool
from lyra.core.hub import Hub, RoutingKey
from lyra.core.message import Platform
from lyra.core.messages import MessageManager
from lyra.core.outbound_dispatcher import OutboundDispatcher
from lyra.core.pairing import PairingConfig, PairingManager, set_pairing_manager
from lyra.stt import STTService, load_stt_config

log = logging.getLogger(__name__)

_CB_DEFAULTS: dict[str, int] = {"failure_threshold": 5, "recovery_timeout": 60}
_CB_SERVICES = ("anthropic", "telegram", "discord", "hub")
_ADMIN_ID_PATTERN = re.compile(r"^(tg|dc):user:\d+$")


def _load_raw_config(config_path: str | None = None) -> dict:
    """Open and parse lyra.toml once; return the raw dict.

    Resolution order: $LYRA_CONFIG env var → 'lyra.toml' in cwd → empty dict.
    """
    raw: dict = {}
    path = config_path or os.environ.get("LYRA_CONFIG", "lyra.toml")
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
        log.info("Loaded config from %s", path)
    except FileNotFoundError:
        log.info("No config file at %s — using defaults", path)
    return raw


def _load_circuit_config(
    raw: dict,
) -> tuple[CircuitRegistry, set[str]]:
    """Load [circuit_breaker.*] and [admin] sections from raw config dict.

    Returns (CircuitRegistry with 4 named CBs, set of admin user_ids).
    Missing sections → all defaults.
    """
    cb_section = raw.get("circuit_breaker", {})
    registry = CircuitRegistry()
    for name in _CB_SERVICES:
        cfg: dict[str, int] = {**_CB_DEFAULTS, **cb_section.get(name, {})}
        registry.register(
            CircuitBreaker(
                name=name,
                failure_threshold=cfg["failure_threshold"],
                recovery_timeout=cfg["recovery_timeout"],
            )
        )

    admin_ids: set[str] = set(raw.get("admin", {}).get("user_ids", []))
    for aid in admin_ids:
        if not _ADMIN_ID_PATTERN.match(aid):
            log.warning(
                "Admin ID %r does not match expected format "
                "(tg|dc):user:<digits> — verify lyra.toml [admin].user_ids",
                aid,
            )
    return registry, admin_ids


def _load_pairing_config(raw: dict) -> PairingConfig:
    """Load [pairing] section from raw config dict. Missing section → all defaults."""
    pairing_section: dict = raw.get("pairing", {})
    return PairingConfig.from_dict(pairing_section)


def _load_messages(language: str = "en") -> MessageManager:
    """Load MessageManager.

    Resolution order:
      LYRA_MESSAGES_CONFIG env var → messages.toml in cwd → bundled config.
    """
    bundled = Path(__file__).resolve().parent / "config" / "messages.toml"
    path_str = os.environ.get("LYRA_MESSAGES_CONFIG") or (
        "messages.toml" if Path("messages.toml").exists() else str(bundled)
    )
    if not path_str.endswith(".toml"):
        log.warning(
            "LYRA_MESSAGES_CONFIG %r does not end with .toml — ignoring, using bundled",
            path_str,
        )
        path_str = str(bundled)
    log.info("Loaded messages from %s", path_str)
    return MessageManager(path_str, language=language)


def _create_agent(
    config: Agent,
    cli_pool: CliPool | None,
    circuit_registry: CircuitRegistry | None = None,
    admin_user_ids: set[str] | None = None,
    msg_manager: MessageManager | None = None,
    stt: STTService | None = None,
) -> AgentBase:
    """Select agent implementation based on backend config."""
    backend = config.model_config.backend
    if backend == "anthropic-sdk":
        from lyra.agents.anthropic_agent import AnthropicAgent
        from lyra.llm.drivers.sdk import AnthropicSdkDriver

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise SystemExit("Missing required env var: ANTHROPIC_API_KEY")
        provider = AnthropicSdkDriver(api_key)
        return AnthropicAgent(
            config,
            provider,
            circuit_registry=circuit_registry,
            admin_user_ids=admin_user_ids,
            msg_manager=msg_manager,
            stt=stt,
        )
    if backend in ("claude-cli", "ollama"):
        if cli_pool is None:
            raise RuntimeError(f"CliPool required for {backend} backend")
        return SimpleAgent(
            config,
            cli_pool,
            circuit_registry=circuit_registry,
            admin_user_ids=admin_user_ids,
            msg_manager=msg_manager,
            stt=stt,
        )
    raise ValueError(f"Unknown backend: {backend}")


def create_health_app(hub: Hub) -> FastAPI:
    """Create a root FastAPI app with /health endpoint for hub monitoring.

    This is the top-level HTTP app — adapter sub-apps can be mounted on it.
    The /health endpoint exposes hub-level health without requiring adapter auth.
    """
    app = FastAPI(title="Lyra Hub")

    @app.get("/health")
    async def health() -> dict:
        uptime_s = time.monotonic() - hub._start_time

        last_message_age_s: float | None = None
        if hub._last_processed_at is not None:
            last_message_age_s = time.monotonic() - hub._last_processed_at

        circuits: dict = {}
        if hub.circuit_registry is not None:
            all_status = hub.circuit_registry.get_all_status()
            circuits = {
                name: {
                    "state": s.state.value,
                    "retry_after": s.retry_after,
                }
                for name, s in all_status.items()
            }

        inbound: dict[str, int] = {
            p.value: hub.inbound_bus.qsize(p)
            for p in hub.inbound_bus.registered_platforms()
        }
        outbound: dict[str, int] = {
            platform.value: dispatcher.qsize()
            for (platform, _bot_id), dispatcher in hub.outbound_dispatchers.items()
        }

        return {
            "queue_size": hub.inbound_bus.staging_qsize(),
            "queues": {"inbound": inbound, "outbound": outbound},
            "last_message_age_s": last_message_age_s,
            "uptime_s": round(uptime_s, 1),
            "circuits": circuits,
        }

    @app.get("/config")
    async def config_endpoint() -> dict:
        from lyra.agents.anthropic_agent import AnthropicAgent

        # TODO(#123): replace isinstance(AnthropicAgent) with a RuntimeConfigCapable
        # protocol check once the LlmProvider abstraction is introduced.
        agent = hub.agent_registry.get("lyra_default")
        if not isinstance(agent, AnthropicAgent):
            raise HTTPException(
                status_code=404,
                detail="runtime config not available for this agent backend",
            )
        rc = agent.runtime_config
        return {
            "style": rc.style,
            "language": rc.language,
            "temperature": rc.temperature,
            "model": rc.model,
            "max_steps": rc.max_steps,
            "extra_instructions": rc.extra_instructions,
            "effective_model": rc.model or agent.config.model_config.model,
            "effective_max_steps": rc.max_steps or agent.config.model_config.max_turns,
        }

    return app


async def _main(*, _stop: asyncio.Event | None = None) -> None:
    """Wire hub + adapters and run until stop event fires.

    The optional _stop parameter is for testing: pass a pre-set Event to exit
    immediately after setup without registering signal handlers.
    """
    load_dotenv()

    raw_config = _load_raw_config()
    circuit_registry, admin_user_ids = _load_circuit_config(raw_config)

    # Config loaders call sys.exit() on missing required env vars — no partial startup.
    tg_cfg = load_telegram_config()
    dc_cfg = load_discord_config()

    agent_config = load_agent_config("lyra_default")
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
    pm: PairingManager | None = None
    if pairing_config.enabled:
        vault_dir = Path.home() / ".lyra"
        vault_dir.mkdir(parents=True, exist_ok=True)
        pm = PairingManager(
            config=pairing_config,
            db_path=vault_dir / "pairing.db",
            admin_user_ids=admin_user_ids,
        )
        await pm.connect()
        set_pairing_manager(pm)

    hub = Hub(
        circuit_registry=circuit_registry,
        msg_manager=msg_manager,
        pairing_manager=pm,
    )

    cli_pool: CliPool | None = None
    if agent_config.model_config.backend == "claude-cli":
        cli_pool = CliPool()
        await cli_pool.start()

    stt_service: STTService | None = None
    if os.environ.get("STT_MODEL_SIZE"):
        try:
            stt_cfg = load_stt_config()
            stt_service = STTService(stt_cfg)
            log.info(
                "STT enabled: model=%s device=%s compute_type=%s",
                stt_cfg.model_size,
                stt_cfg.device,
                stt_cfg.compute_type,
            )
        except ValueError as exc:
            raise SystemExit(f"Invalid STT configuration: {exc}") from exc

    agent = _create_agent(
        agent_config,
        cli_pool,
        circuit_registry=circuit_registry,
        admin_user_ids=admin_user_ids,
        msg_manager=msg_manager,
        stt=stt_service,
    )
    hub.register_agent(agent)

    tg_adapter = TelegramAdapter(
        bot_id="main",
        token=tg_cfg.token,
        hub=hub,
        bot_username=tg_cfg.bot_username,
        webhook_secret=tg_cfg.webhook_secret,
        circuit_registry=circuit_registry,
        msg_manager=msg_manager,
    )
    dc_adapter = DiscordAdapter(
        hub=hub,
        bot_id="main",
        circuit_registry=circuit_registry,
        msg_manager=msg_manager,
        auto_thread=dc_cfg.auto_thread,
    )

    hub.register_adapter(Platform.TELEGRAM, "main", tg_adapter)
    hub.register_adapter(Platform.DISCORD, "main", dc_adapter)
    tg_key = RoutingKey(Platform.TELEGRAM, "main", "*")
    dc_key = RoutingKey(Platform.DISCORD, "main", "*")
    hub.register_binding(
        Platform.TELEGRAM, "main", "*", agent.name, tg_key.to_pool_id()
    )
    hub.register_binding(Platform.DISCORD, "main", "*", agent.name, dc_key.to_pool_id())

    # Create and register per-platform outbound dispatchers
    tg_dispatcher = OutboundDispatcher(
        platform_name="telegram",
        adapter=tg_adapter,
        circuit=circuit_registry.get("telegram"),
        circuit_registry=circuit_registry,
    )
    dc_dispatcher = OutboundDispatcher(
        platform_name="discord",
        adapter=dc_adapter,
        circuit=circuit_registry.get("discord"),
        circuit_registry=circuit_registry,
    )
    hub.register_outbound_dispatcher(Platform.TELEGRAM, "main", tg_dispatcher)
    hub.register_outbound_dispatcher(Platform.DISCORD, "main", dc_dispatcher)

    # Start InboundBus feeder tasks and OutboundDispatcher workers
    await hub.inbound_bus.start()
    await tg_dispatcher.start()
    await dc_dispatcher.start()

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

    tasks = [
        asyncio.create_task(hub.run(), name="hub"),
        asyncio.create_task(
            # handle_signals=False is mandatory when aiogram runs inside
            # asyncio.gather() — otherwise it installs its own signal handlers
            # that conflict with the event loop's, causing double-cancellation.
            tg_adapter.dp.start_polling(tg_adapter.bot, handle_signals=False),
            name="telegram",
        ),
        asyncio.create_task(dc_adapter.start(dc_cfg.token), name="discord"),
        asyncio.create_task(health_server.serve(), name="health"),
    ]

    log.info(
        "Lyra started — Telegram + Discord adapters running, health on :%d.",
        health_port,
    )

    await stop.wait()

    log.info("Shutdown signal received — stopping…")
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await hub.inbound_bus.stop()
    await tg_dispatcher.stop()
    await dc_dispatcher.stop()
    await dc_adapter.close()
    if pm is not None:
        await pm.close()
    if cli_pool is not None:
        await cli_pool.stop()
    log.info("Lyra stopped.")


def _setup_logging() -> None:
    """Configure logging: console + rotating file in ~/.lyra/logs/."""
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"

    log_dir = Path.home() / ".lyra" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"{stamp}_lyra.log"

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,  # 10 MB per file, 5 backups
    )
    file_handler.setFormatter(logging.Formatter(fmt))

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(fmt))

    logging.basicConfig(level=logging.INFO, handlers=[file_handler, console_handler])

    logging.getLogger(__name__).info("Logging to %s", log_file)


def main() -> None:
    _setup_logging()
    asyncio.run(_main())


if __name__ == "__main__":
    main()
