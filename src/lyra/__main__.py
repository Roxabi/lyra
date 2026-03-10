"""Entry point: python -m lyra.

Starts Telegram + Discord adapters in one event loop.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import tomllib
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

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

log = logging.getLogger(__name__)

_CB_DEFAULTS: dict[str, int] = {"failure_threshold": 5, "recovery_timeout": 60}
_CB_SERVICES = ("anthropic", "telegram", "discord", "hub")


def _load_circuit_config(
    config_path: str | None = None,
) -> tuple[CircuitRegistry, set[str]]:
    """Load [circuit_breaker.*] and [admin] sections from TOML config.

    Returns (CircuitRegistry with 4 named CBs, set of admin user_ids).
    Missing sections or missing config file → all defaults.
    Config path: $LYRA_CONFIG env var → 'lyra.toml' in cwd → all defaults.
    """
    import os

    raw: dict = {}
    path = config_path or os.environ.get("LYRA_CONFIG", "lyra.toml")
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
        log.info("Loaded circuit config from %s", path)
    except FileNotFoundError:
        log.info("No config file at %s — using circuit breaker defaults", path)

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
    return registry, admin_ids


def _load_messages(language: str = "en") -> MessageManager:
    """Load MessageManager.

    Resolution order:
      LYRA_MESSAGES_CONFIG env var → messages.toml in cwd → bundled config.
    """
    import os

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
) -> AgentBase:
    """Select agent implementation based on backend config."""
    backend = config.model_config.backend
    if backend == "anthropic-sdk":
        from lyra.agents.anthropic_agent import AnthropicAgent

        return AnthropicAgent(
            config,
            circuit_registry=circuit_registry,
            admin_user_ids=admin_user_ids,
            msg_manager=msg_manager,
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
        )
    raise ValueError(f"Unknown backend: {backend}")


async def _main(*, _stop: asyncio.Event | None = None) -> None:
    """Wire hub + adapters and run until stop event fires.

    The optional _stop parameter is for testing: pass a pre-set Event to exit
    immediately after setup without registering signal handlers.
    """
    load_dotenv()

    circuit_registry, admin_user_ids = _load_circuit_config()

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

    hub = Hub(circuit_registry=circuit_registry, msg_manager=msg_manager)

    cli_pool: CliPool | None = None
    if agent_config.model_config.backend == "claude-cli":
        cli_pool = CliPool()
        await cli_pool.start()

    agent = _create_agent(
        agent_config,
        cli_pool,
        circuit_registry=circuit_registry,
        admin_user_ids=admin_user_ids,
        msg_manager=msg_manager,
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
    )

    hub.register_adapter(Platform.TELEGRAM, "main", tg_adapter)
    hub.register_adapter(Platform.DISCORD, "main", dc_adapter)
    tg_key = RoutingKey(Platform.TELEGRAM, "main", "*")
    dc_key = RoutingKey(Platform.DISCORD, "main", "*")
    hub.register_binding(
        Platform.TELEGRAM, "main", "*", agent.name, tg_key.to_pool_id()
    )
    hub.register_binding(Platform.DISCORD, "main", "*", agent.name, dc_key.to_pool_id())

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
    ]

    log.info("Lyra started — Telegram + Discord adapters running.")

    await stop.wait()

    log.info("Shutdown signal received — stopping…")
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await dc_adapter.close()
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
