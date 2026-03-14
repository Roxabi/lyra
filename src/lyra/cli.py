"""lyra-agent CLI — create / list / validate agent TOML configs."""

from __future__ import annotations

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

app = typer.Typer(name="lyra-agent", help="Manage Lyra agent configurations.")

_DEFAULT_TOOLS = ["Read", "Grep", "Glob", "WebFetch", "WebSearch"]

_AGENTS_DIR_OPT: Optional[Path] = typer.Option(
    None, help="Directory where agent TOMLs live (skips location prompt)."
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_tools(raw: str) -> list[str]:
    """Parse tools input: blank → [], 'default' → standard set, else split."""
    stripped = raw.strip()
    if not stripped:
        return []
    if stripped.lower() == "default":
        return list(_DEFAULT_TOOLS)
    return [t.strip() for t in stripped.split(",") if t.strip()]


def _prompt_location() -> Path:
    """Ask where to save the new agent TOML: user or system."""
    typer.echo("  [u] user   — ~/.lyra/agents/      (personal, gitignored)")
    typer.echo(f"  [s] system — {AGENTS_DIR}  (versioned)")
    choice = typer.prompt("Save to", default="u", show_default=True)
    if choice.lower().startswith("s"):
        return _SYSTEM_AGENTS_DIR
    return _USER_AGENTS_DIR


def _prompt_sr_subconfig(
    backend: str,
) -> tuple[bool, int | None, list[str], dict[str, str]]:
    """Prompt for smart routing settings. Returns (enabled, history, cmds, models)."""
    sr_user = typer.confirm("Enable smart routing?", default=False)

    if not sr_user:
        return False, None, [], {}

    if backend == "claude-cli":
        typer.echo(
            "Warning: smart routing requires backend=anthropic-sdk — "
            "forcing smart_routing.enabled to false."
        )
        return False, None, [], {}

    # anthropic-sdk: ask sub-prompts
    history: int = typer.prompt("SR history size", default=50, type=int)

    high_raw = typer.prompt("SR high-complexity commands (blank = none)", default="")
    high_cmds = [c.strip() for c in high_raw.split(",") if c.strip()]

    sr_models: dict[str, str] = {}
    for tier in ("trivial", "simple", "moderate", "complex"):
        m = typer.prompt(f"SR model for {tier} tier (blank = skip)", default="")
        if m.strip():
            sr_models[tier] = m.strip()

    return True, history, high_cmds, sr_models


def _build_toml(  # noqa: PLR0913 — one arg per config key, intentional
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
    """Render agent config as a TOML string using tomli_w (no injection risk)."""
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


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


@app.command()  # noqa: C901
def create(
    agents_dir: Optional[Path] = _AGENTS_DIR_OPT,
) -> None:
    """Interactively create a new agent TOML configuration."""
    # 1. Agent name
    name = typer.prompt("Agent name")
    if not re.match(r"^[a-zA-Z0-9_-]+$", name):
        typer.echo(f"Error: invalid agent name {name!r} — only [a-zA-Z0-9_-] allowed")
        raise typer.Exit(1)

    # 2. Location — ask only when --agents-dir was not explicitly provided
    target_dir: Path = agents_dir if agents_dir is not None else _prompt_location()

    target_dir.mkdir(parents=True, exist_ok=True)

    toml_path = target_dir / f"{name}.toml"
    if toml_path.exists():
        typer.echo(f"Error: agent {name!r} already exists at {toml_path}")
        raise typer.Exit(1)

    # 3. Backend
    backend = typer.prompt(
        "Backend",
        type=click.Choice(["claude-cli", "anthropic-sdk"]),
    )
    # 4. Model
    model = typer.prompt("Model", default="claude-sonnet-4-5")
    # 5. Working directory (blank = omit — falls back to config.toml [defaults].cwd)
    cwd_raw = typer.prompt(
        "Working directory (blank = inherit from config.toml [defaults])", default=""
    )
    # 6. Max turns
    max_turns: int = typer.prompt("Max turns", default=10, type=int)
    # 7. Tools
    tools_raw = typer.prompt(
        'Tools (blank=none, "default"=standard, or comma-separated)', default=""
    )
    tools = _parse_tools(tools_raw)
    # 8. Persona name (blank = omit)
    persona_raw = typer.prompt("Persona name (blank to skip)", default="")
    # 9. Show intermediate turns?
    show_intermediate = typer.confirm("Show intermediate turns?", default=False)
    # 10. Smart routing
    sr_enabled, sr_history, sr_high_cmds, sr_models = _prompt_sr_subconfig(backend)
    # 11. Plugins
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
    typer.echo(f"  lyra-agent validate {name}")
    typer.echo("  lyra-agent list")


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@app.command(name="list")
def list_agents(
    agents_dir: Optional[Path] = _AGENTS_DIR_OPT,
) -> None:
    """List all configured agents (user and system, or --agents-dir)."""
    header = (
        f"{'NAME':<16} {'BACKEND':<16} {'MODEL':<34} {'SMART ROUTING':<14} {'SOURCE'}"
    )
    typer.echo(header)

    if agents_dir is not None:
        # Explicit dir: list only that dir (backward compat / tests)
        _list_from_dir(agents_dir, source_label=None)
        return

    # Default: show user agents first, then system agents not overridden by user
    user_names: set[str] = set()
    if _USER_AGENTS_DIR.exists():
        user_names = _list_from_dir(_USER_AGENTS_DIR, source_label="user")

    if _SYSTEM_AGENTS_DIR.exists():
        _list_from_dir(_SYSTEM_AGENTS_DIR, source_label="system", skip=user_names)


def _list_from_dir(
    directory: Path,
    source_label: str | None,
    skip: set[str] | None = None,
) -> set[str]:
    """Print agent rows from a directory. Returns the set of names printed."""
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
# validate
# ---------------------------------------------------------------------------


@app.command()
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
            backend = cfg.model_config.backend
            typer.echo(
                f"Warning: smart_routing.enabled=true but backend={backend!r} "
                "— smart routing will be ignored at runtime"
            )
        else:
            typer.echo("smart_routing: enabled (anthropic-sdk constraint satisfied)")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point."""
    app()
