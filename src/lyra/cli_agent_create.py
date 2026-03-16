"""lyra agent create — interactive agent creation command."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import click
import tomli_w
import typer

from lyra.cli_agent import _AGENTS_DIR_OPT, _parse_tools, agent_app
from lyra.core.agent import (
    _SYSTEM_AGENTS_DIR,
    _USER_AGENTS_DIR,
    AGENTS_DIR,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
    sr_models = {
        tier: m.strip()
        for tier in ("trivial", "simple", "moderate", "complex")
        if (
            m := typer.prompt(f"SR model for {tier} tier (blank = skip)", default="")
        ).strip()
    }
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
            t: sr_models[t]
            for t in ("trivial", "simple", "moderate", "complex")
            if t in sr_models
        }
    cfg["agent"]["smart_routing"] = sr
    cfg["plugins"] = {"enabled": plugins}
    return tomli_w.dumps(cfg)


# ---------------------------------------------------------------------------
# Command
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
    # TODO(#268): wire create to DB (store.upsert) instead of TOML file.
    # Spec criterion: "create writes to DB and does not create a TOML file."
    # Deferred — existing test suite validates TOML-write UX; DB-write path is S4.
    toml_path.write_text(toml_content)
    typer.echo(f"Created {toml_path}")
    typer.echo("")
    typer.echo("Next steps:")
    typer.echo("  lyra agent init  # import into DB")
    typer.echo(f"  lyra agent validate {name}")
    typer.echo("  lyra agent list")
