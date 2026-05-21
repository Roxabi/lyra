"""lyra setup — one-time platform setup commands (#291)."""

from __future__ import annotations

import asyncio
import logging
import tomllib
from typing import Any

import typer

from lyra.core.paths import PLUGINS_DIR

log = logging.getLogger(__name__)

setup_app = typer.Typer(name="setup", help="One-time platform setup commands.")


@setup_app.command(name="commands")
def cmd_setup_commands(
    config: str = typer.Option(  # noqa: B008 — DEBT:typer-default-option
        "config.toml", "--config", "-c", help="Path to config.toml."
    ),
) -> None:
    """Register commands with platform-native menus (Telegram setMyCommands)."""
    asyncio.run(_register_all(config))


async def _register_telegram_bot(
    bot_id: str,
    token: str,
    public_commands: list,
) -> str:
    """Call set_my_commands for a single bot. Returns bot username."""
    from aiogram import Bot
    from aiogram.types import BotCommand

    bot = Bot(token=token)
    try:
        bot_commands = [
            BotCommand(
                command=cmd.name.lstrip("/"),
                description=cmd.description[:256],
            )
            for cmd in public_commands
        ]
        await bot.set_my_commands(bot_commands)
        me = await bot.get_me()
        return me.username or bot_id
    finally:
        await bot.session.close()


async def _register_bot(
    bot_cfg: dict[str, Any],
    raw: dict[str, Any],
    command_loader: Any,
    voice_commands: list,
) -> bool:
    """Register commands for a single Telegram bot. Returns True on error."""
    from lyra.bootstrap import credentials
    from lyra.core.commands.command_registry import collect_commands
    from lyra.core.commands.command_router import CommandRouter
    from lyra.errors import MissingCredentialsError

    bot_id = bot_cfg.get("bot_id", "unknown")
    agent_name = bot_cfg.get("agent", "")

    # Load plugins for this bot's agent
    agent_overrides = raw.get("agents", {}).get(agent_name, {})
    _commands_enabled = agent_overrides.get("commands", {}).get("enabled")
    _plugins_enabled = agent_overrides.get("plugins", {}).get("enabled")
    if _commands_enabled is not None:
        enabled_plugins: list[str] = _commands_enabled
    elif _plugins_enabled is not None:
        log.debug(
            "Agent %s: config.toml uses deprecated [plugins].enabled key;"
            " rename to [commands].enabled",
            agent_name,
        )
        enabled_plugins = _plugins_enabled
    else:
        enabled_plugins = []
    for plugin_name in enabled_plugins:
        try:
            command_loader.load(plugin_name)
        except Exception:  # noqa: BLE001 — DEBT:boundary-broad-catch — resilient: plugin load failure must not abort agent setup
            log.warning(
                "Could not load plugin %s for agent %s",
                plugin_name,
                agent_name,
            )

    # Collect command metadata (uses CommandRouter for admin detection)
    builtin_meta = list(CommandRouter.builtin_metadata())
    plugin_descs = command_loader.get_command_descriptions(enabled_plugins)
    all_commands = collect_commands(builtin_meta, plugin_descs, voice_commands)
    public_commands = [cmd for cmd in all_commands if not cmd.admin_only]

    # Resolve token from /run/secrets
    try:
        token, _ = credentials.load_bot_token("telegram", bot_id)
    except MissingCredentialsError as exc:
        typer.echo(f"Error: {exc}", err=True)
        return True

    # Register with Telegram
    try:
        username = await _register_telegram_bot(bot_id, token, public_commands)
        typer.echo(
            f"Registered {len(public_commands)} commands for bot @{username} ({bot_id})"
        )
    except Exception as exc:  # noqa: BLE001  — DEBT:boundary-broad-catch# top-level boundary
        typer.echo(
            f"Error registering commands for bot_id={bot_id}: {exc}",
            err=True,
        )
        return True
    return False


async def _register_all(config_path: str) -> None:
    """For each Telegram bot: resolve token, collect commands, set_my_commands."""
    from lyra.adapters.discord.voice.discord_voice_commands import VOICE_COMMANDS
    from lyra.core.commands.command_loader import CommandLoader

    try:
        with open(config_path, "rb") as f:
            raw = tomllib.load(f)
    except FileNotFoundError:
        typer.echo(f"Error: config file not found: {config_path}", err=True)
        raise typer.Exit(1)

    tg_bots = raw.get("telegram", {}).get("bots", [])
    if not tg_bots:
        typer.echo("No Telegram bots configured.")
        return

    command_loader = CommandLoader(PLUGINS_DIR)

    errors = 0
    for bot_cfg in tg_bots:
        had_error = await _register_bot(bot_cfg, raw, command_loader, VOICE_COMMANDS)
        if had_error:
            errors += 1

    if errors:
        raise typer.Exit(1)
