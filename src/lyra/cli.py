"""lyra — unified CLI for Lyra by Roxabi.

Entry points:
    lyra                     → start the server
    lyra start               → explicit start
    lyra --version / -V      → print version
    lyra agent create        → interactive agent wizard
    lyra agent list          → list all agents
    lyra agent validate      → validate agent TOML
    lyra config show         → display parsed config.toml
    lyra config validate     → validate config.toml

Backward compat:
    lyra-agent               → deprecated alias for `lyra agent`
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import os
import re
import tomllib
from pathlib import Path
from typing import Optional

import click
import tomli_w
import typer

from lyra.core.agent import (
    _SYSTEM_AGENTS_DIR,
    _USER_AGENTS_DIR,
    AGENTS_DIR,
    load_agent_config,
)

# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------

try:
    _VERSION = importlib.metadata.version("lyra")
except importlib.metadata.PackageNotFoundError:
    _VERSION = "0.0.0-dev"

# ---------------------------------------------------------------------------
# App tree
# ---------------------------------------------------------------------------

lyra_app = typer.Typer(
    name="lyra",
    help="Lyra by Roxabi — personal AI agent engine.",
    no_args_is_help=False,
)
agent_app = typer.Typer(name="agent", help="Manage agent configurations.")
config_app = typer.Typer(name="config", help="Manage instance config (config.toml).")

lyra_app.add_typer(agent_app, name="agent")
lyra_app.add_typer(config_app, name="config")

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_DEFAULT_TOOLS = ["Read", "Grep", "Glob", "WebFetch", "WebSearch"]

_AGENTS_DIR_OPT: Optional[Path] = typer.Option(
    None, help="Directory where agent TOMLs live (skips location prompt)."
)


def _parse_tools(raw: str) -> list[str]:
    stripped = raw.strip()
    if not stripped:
        return []
    if stripped.lower() == "default":
        return list(_DEFAULT_TOOLS)
    return [t.strip() for t in stripped.split(",") if t.strip()]


def _prompt_location() -> Path:
    typer.echo("  [u] user   — ~/.lyra/agents/      (personal, gitignored)")
    typer.echo(f"  [s] system — {AGENTS_DIR}  (versioned)")
    choice = typer.prompt("Save to", default="u", show_default=True)
    if choice.lower().startswith("s"):
        return _SYSTEM_AGENTS_DIR
    return _USER_AGENTS_DIR


def _prompt_sr_subconfig(
    backend: str,
) -> tuple[bool, int | None, list[str], dict[str, str]]:
    sr_user = typer.confirm("Enable smart routing?", default=False)
    if not sr_user:
        return False, None, [], {}
    if backend == "claude-cli":
        typer.echo(
            "Warning: smart routing requires backend=anthropic-sdk — "
            "forcing smart_routing.enabled to false."
        )
        return False, None, [], {}
    history: int = typer.prompt("SR history size", default=50, type=int)
    high_raw = typer.prompt("SR high-complexity commands (blank = none)", default="")
    high_cmds = [c.strip() for c in high_raw.split(",") if c.strip()]
    sr_models: dict[str, str] = {}
    for tier in ("trivial", "simple", "moderate", "complex"):
        m = typer.prompt(f"SR model for {tier} tier (blank = skip)", default="")
        if m.strip():
            sr_models[tier] = m.strip()
    return True, history, high_cmds, sr_models


def _build_toml(  # noqa: PLR0913
    name: str,
    backend: str,
    model: str,
    cwd_raw: str,
    max_turns: int,
    tools: list[str],
    persona_raw: str,
    show_intermediate: bool,
    sr_enabled: bool,
    sr_history: int | None,
    sr_high_cmds: list[str],
    sr_models: dict[str, str],
    plugins: list[str],
) -> str:
    cfg: dict = {
        "agent": {
            "name": name,
            "memory_namespace": name,
            "permissions": [],
            "show_intermediate": show_intermediate,
        }
    }
    if persona_raw.strip():
        cfg["agent"]["persona"] = persona_raw.strip()
    cfg["model"] = {
        "backend": backend,
        "model": model,
        "max_turns": max_turns,
        "tools": tools,
    }
    if cwd_raw.strip():
        cfg["model"]["cwd"] = cwd_raw.strip()
    sr: dict = {"enabled": sr_enabled}
    if sr_enabled and sr_history is not None:
        sr["history_size"] = sr_history
    if sr_enabled and sr_high_cmds:
        sr["high_complexity_commands"] = sr_high_cmds
    if sr_enabled and sr_models:
        sr["models"] = {
            tier: sr_models[tier]
            for tier in ("trivial", "simple", "moderate", "complex")
            if tier in sr_models
        }
    cfg["agent"]["smart_routing"] = sr
    cfg["plugins"] = {"enabled": plugins}
    return tomli_w.dumps(cfg)


def _list_from_dir(
    directory: Path,
    source_label: str | None,
    skip: set[str] | None = None,
) -> set[str]:
    printed: set[str] = set()
    if not directory.exists():
        return printed
    for toml_file in sorted(directory.glob("*.toml")):
        agent_name = toml_file.stem
        if skip and agent_name in skip:
            continue
        try:
            cfg = load_agent_config(agent_name, agents_dir=directory)
        except Exception as e:
            typer.echo(f"  [warn] skipped {toml_file.name}: {e}", err=True)
            continue
        sr_status = (
            "enabled" if cfg.smart_routing and cfg.smart_routing.enabled else "disabled"
        )
        source = f"  {source_label}" if source_label else ""
        typer.echo(
            f"{cfg.name:<16} {cfg.model_config.backend:<16} "
            f"{cfg.model_config.model:<34} {sr_status:<14}{source}"
        )
        printed.add(agent_name)
    return printed


# ---------------------------------------------------------------------------
# lyra (root)
# ---------------------------------------------------------------------------


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"lyra {_VERSION}")
        raise typer.Exit()


@lyra_app.callback(invoke_without_command=True)
def _root_callback(
    ctx: typer.Context,
    version: bool = typer.Option(  # noqa: B008
        False,
        "--version",
        "-V",
        help="Print version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """Lyra by Roxabi — personal AI agent engine.

    Run without a subcommand to start the server.
    """
    if ctx.invoked_subcommand is None:
        _run_server()


# ---------------------------------------------------------------------------
# lyra start
# ---------------------------------------------------------------------------


@lyra_app.command()
def start() -> None:
    """Start the Lyra server (Telegram + Discord adapters)."""
    _run_server()


def _run_server() -> None:
    from lyra.__main__ import _main  # local import — avoids heavy deps at import time

    asyncio.run(_main())


# ---------------------------------------------------------------------------
# lyra agent
# ---------------------------------------------------------------------------


@agent_app.command()  # noqa: C901
def create(
    agents_dir: Optional[Path] = _AGENTS_DIR_OPT,
) -> None:
    """Interactively create a new agent TOML configuration."""
    name = typer.prompt("Agent name")
    if not re.match(r"^[a-zA-Z0-9_-]+$", name):
        typer.echo(f"Error: invalid agent name {name!r} — only [a-zA-Z0-9_-] allowed")
        raise typer.Exit(1)

    target_dir: Path = agents_dir if agents_dir is not None else _prompt_location()
    target_dir.mkdir(parents=True, exist_ok=True)

    toml_path = target_dir / f"{name}.toml"
    if toml_path.exists():
        typer.echo(f"Error: agent {name!r} already exists at {toml_path}")
        raise typer.Exit(1)

    backend = typer.prompt(
        "Backend", type=click.Choice(["claude-cli", "anthropic-sdk"])
    )
    model = typer.prompt("Model", default="claude-sonnet-4-5")
    cwd_raw = typer.prompt(
        "Working directory (blank = inherit from config.toml [defaults])", default=""
    )
    max_turns: int = typer.prompt("Max turns", default=10, type=int)
    tools_raw = typer.prompt(
        'Tools (blank=none, "default"=standard, or comma-separated)', default=""
    )
    tools = _parse_tools(tools_raw)
    persona_raw = typer.prompt("Persona name (blank to skip)", default="")
    show_intermediate = typer.confirm("Show intermediate turns?", default=False)
    sr_enabled, sr_history, sr_high_cmds, sr_models = _prompt_sr_subconfig(backend)
    plugins_raw = typer.prompt("Plugins (blank = none, or comma-separated)", default="")
    plugins = [p.strip() for p in plugins_raw.split(",") if p.strip()]

    toml_content = _build_toml(
        name=name,
        backend=backend,
        model=model,
        cwd_raw=cwd_raw,
        max_turns=max_turns,
        tools=tools,
        persona_raw=persona_raw,
        show_intermediate=show_intermediate,
        sr_enabled=sr_enabled,
        sr_history=sr_history,
        sr_high_cmds=sr_high_cmds,
        sr_models=sr_models,
        plugins=plugins,
    )
    toml_path.write_text(toml_content)
    typer.echo(f"Created {toml_path}")
    typer.echo("")
    typer.echo("Next steps:")
    typer.echo(f"  lyra agent validate {name}")
    typer.echo("  lyra agent list")


@agent_app.command(name="list")
def list_agents(
    agents_dir: Optional[Path] = _AGENTS_DIR_OPT,
) -> None:
    """List all configured agents (user and system)."""
    typer.echo(
        f"{'NAME':<16} {'BACKEND':<16} {'MODEL':<34} {'SMART ROUTING':<14} {'SOURCE'}"
    )
    if agents_dir is not None:
        _list_from_dir(agents_dir, source_label=None)
        return
    user_names = _list_from_dir(_USER_AGENTS_DIR, source_label="user")
    _list_from_dir(_SYSTEM_AGENTS_DIR, source_label="system", skip=user_names)


@agent_app.command()
def validate(
    name: str = typer.Argument(..., help="Agent name to validate."),
    agents_dir: Optional[Path] = _AGENTS_DIR_OPT,
) -> None:
    """Validate an agent TOML configuration against the schema."""
    try:
        cfg = load_agent_config(name, agents_dir=agents_dir)
    except FileNotFoundError as e:
        typer.echo(f"Error: {e}")
        raise typer.Exit(1)
    except (ValueError, tomllib.TOMLDecodeError) as e:
        typer.echo(f"Error: schema error — {e}")
        raise typer.Exit(1)

    typer.echo("Schema: OK")
    if cfg.smart_routing and cfg.smart_routing.enabled:
        if cfg.model_config.backend != "anthropic-sdk":
            typer.echo(
                f"Warning: smart_routing.enabled=true but "
                f"backend={cfg.model_config.backend!r} "
                "— smart routing will be ignored at runtime"
            )
        else:
            typer.echo("smart_routing: enabled (anthropic-sdk constraint satisfied)")


# ---------------------------------------------------------------------------
# lyra config
# ---------------------------------------------------------------------------

_CONFIG_PATH_OPT: str = typer.Option(
    "config.toml", "--config", "-c", help="Path to config.toml."
)


def _load_raw(path: str) -> dict:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        typer.echo(f"Error: config file not found: {path}")
        raise typer.Exit(1)
    except tomllib.TOMLDecodeError as e:
        typer.echo(f"Error: TOML parse error — {e}")
        raise typer.Exit(1)


@config_app.command(name="show")
def config_show(
    config: str = _CONFIG_PATH_OPT,
) -> None:
    """Display the parsed config.toml."""
    raw = _load_raw(config)

    defaults = raw.get("defaults", {})
    if defaults:
        typer.echo("[defaults]")
        for k, v in defaults.items():
            typer.echo(f"  {k} = {v!r}")

    admin_ids = raw.get("admin", {}).get("user_ids", [])
    typer.echo(f"\n[admin]  {len(admin_ids)} user(s)")
    for uid in admin_ids:
        typer.echo(f"  {uid}")

    tg_bots = raw.get("telegram", {}).get("bots", [])
    typer.echo(f"\n[telegram]  {len(tg_bots)} bot(s)")
    for b in tg_bots:
        typer.echo(f"  {b.get('bot_id')}  →  agent={b.get('agent')}")

    dc_bots = raw.get("discord", {}).get("bots", [])
    typer.echo(f"\n[discord]  {len(dc_bots)} bot(s)")
    for b in dc_bots:
        typer.echo(f"  {b.get('bot_id')}  →  agent={b.get('agent')}")

    agent_overrides = raw.get("agents", {})
    if agent_overrides:
        typer.echo(f"\n[agents]  {len(agent_overrides)} override(s)")
        for aname, vals in agent_overrides.items():
            typer.echo(f"  {aname}: {vals}")


@config_app.command(name="validate")
def config_validate(
    config: str = _CONFIG_PATH_OPT,
) -> None:
    """Validate config.toml (bots, env vars, auth sections)."""
    from lyra.config import load_multibot_config

    raw = _load_raw(config)

    errors: list[str] = []
    for section in ("telegram", "discord"):
        for bot in raw.get(section, {}).get("bots", []):
            for field in ("token", "webhook_secret", "bot_username"):
                val = bot.get(field, "")
                if isinstance(val, str) and val.startswith("env:"):
                    env_var = val[4:]
                    if not os.environ.get(env_var):
                        errors.append(
                            f"[{section}.bots] bot_id={bot.get('bot_id')!r}: "
                            f"env var {env_var!r} is not set"
                        )

    try:
        load_multibot_config(raw)
    except ValueError as e:
        errors.append(str(e))

    if errors:
        for err in errors:
            typer.echo(f"Warning: {err}")
        raise typer.Exit(1)

    typer.echo("config.toml: OK")
    tg_bots = raw.get("telegram", {}).get("bots", [])
    dc_bots = raw.get("discord", {}).get("bots", [])
    typer.echo(f"  {len(tg_bots)} Telegram bot(s), {len(dc_bots)} Discord bot(s)")


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def lyra_main() -> None:
    """Entry point for the `lyra` CLI."""
    lyra_app()


def agent_main() -> None:
    """Deprecated entry point — use `lyra agent` instead."""
    typer.echo(
        "Warning: lyra-agent is deprecated, use 'lyra agent ...' instead.", err=True
    )
    agent_app()


# Keep old name for any direct imports
main = lyra_main
