"""lyra-agent CLI — create / list / validate agent TOML configs."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

import typer

from lyra.core.agent import _AGENTS_DIR, load_agent_config

app = typer.Typer(name="lyra-agent", help="Manage Lyra agent configurations.")

_DEFAULT_TOOLS = ["Read", "Grep", "Glob", "WebFetch", "WebSearch"]

_AGENTS_DIR_OPT = typer.Option(
    _AGENTS_DIR, help="Directory where agent TOMLs live."
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _toml_list(items: list[str]) -> str:
    """Format a Python list as a TOML inline array."""
    if not items:
        return "[]"
    inner = ", ".join(f'"{item}"' for item in items)
    return f"[{inner}]"


def _parse_tools(raw: str) -> list[str]:
    """Parse tools input: blank → [], 'default' → standard set, else split."""
    stripped = raw.strip()
    if not stripped:
        return []
    if stripped.lower() == "default":
        return list(_DEFAULT_TOOLS)
    return [t.strip() for t in stripped.split(",") if t.strip()]


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
    history_raw = typer.prompt("SR history size (blank = 50)", default="")
    history = int(history_raw) if history_raw.strip() else 50

    high_raw = typer.prompt(
        "SR high-complexity commands (blank = none)", default=""
    )
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
    """Render agent config as a TOML string."""
    lines: list[str] = []

    # [agent]
    lines.append("[agent]")
    lines.append(f'name = "{name}"')
    lines.append(f'memory_namespace = "{name}"')
    if persona_raw.strip():
        lines.append(f'persona = "{persona_raw.strip()}"')
    lines.append("permissions = []")
    lines.append(f"show_intermediate = {str(show_intermediate).lower()}")
    lines.append("")

    # [model]
    lines.append("[model]")
    lines.append(f'backend = "{backend}"')
    lines.append(f'model = "{model}"')
    if cwd_raw.strip():
        lines.append(f'cwd = "{cwd_raw.strip()}"')
    lines.append(f"max_turns = {max_turns}")
    lines.append(f"tools = {_toml_list(tools)}")
    lines.append("")

    # [agent.smart_routing]
    lines.append("[agent.smart_routing]")
    lines.append(f"enabled = {str(sr_enabled).lower()}")
    if sr_enabled and sr_history is not None:
        lines.append(f"history_size = {sr_history}")
    if sr_enabled and sr_high_cmds:
        lines.append(f"high_complexity_commands = {_toml_list(sr_high_cmds)}")
    if sr_enabled and sr_models:
        lines.append("")
        lines.append("[agent.smart_routing.models]")
        for tier, model_id in sr_models.items():
            lines.append(f'{tier} = "{model_id}"')
    lines.append("")

    # [plugins]
    lines.append("[plugins]")
    lines.append(f"enabled = {_toml_list(plugins)}")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


@app.command()  # noqa: C901
def create(
    agents_dir: Path = _AGENTS_DIR_OPT,
) -> None:
    """Interactively create a new agent TOML configuration."""
    # 1. Agent name
    name = typer.prompt("Agent name")
    if not re.match(r"^[a-zA-Z0-9_-]+$", name):
        typer.echo(
            f"Error: invalid agent name {name!r} — only [a-zA-Z0-9_-] allowed"
        )
        raise typer.Exit(1)

    agents_dir.mkdir(parents=True, exist_ok=True)

    toml_path = agents_dir / f"{name}.toml"
    if toml_path.exists():
        typer.echo(f"Error: agent {name!r} already exists at {toml_path}")
        raise typer.Exit(1)

    # 2. Backend
    backend = typer.prompt("Backend (claude-cli / anthropic-sdk)")
    # 3. Model
    model = typer.prompt("Model", default="claude-sonnet-4-5")
    # 4. Working directory (blank = omit)
    cwd_raw = typer.prompt("Working directory (blank to skip)", default="")
    # 5. Max turns
    max_turns_raw = typer.prompt("Max turns (blank = 10)", default="")
    max_turns = int(max_turns_raw) if max_turns_raw.strip() else 10
    # 6. Tools
    tools_raw = typer.prompt(
        'Tools (blank=none, "default"=standard, or comma-separated)', default=""
    )
    tools = _parse_tools(tools_raw)
    # 7. Persona name (blank = omit)
    persona_raw = typer.prompt("Persona name (blank to skip)", default="")
    # 8. Show intermediate turns?
    show_intermediate = typer.confirm("Show intermediate turns?", default=False)
    # 9. Smart routing
    sr_enabled, sr_history, sr_high_cmds, sr_models = _prompt_sr_subconfig(backend)
    # 10. Plugins
    plugins_raw = typer.prompt(
        "Plugins (blank = none, or comma-separated)", default=""
    )
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
    typer.echo(f"  lyra-agent validate {name} --agents-dir {agents_dir}")
    typer.echo(f"  lyra-agent list --agents-dir {agents_dir}")


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@app.command(name="list")
def list_agents(
    agents_dir: Path = _AGENTS_DIR_OPT,
) -> None:
    """List all configured agents."""
    header = (
        f"{'NAME':<16} {'BACKEND':<16} {'MODEL':<34} {'SMART ROUTING'}"
    )
    typer.echo(header)

    if not agents_dir.exists():
        return

    for toml_file in sorted(agents_dir.glob("*.toml")):
        agent_name = toml_file.stem
        try:
            cfg = load_agent_config(agent_name, agents_dir=agents_dir)
        except Exception:
            continue

        sr_status = (
            "enabled"
            if cfg.smart_routing and cfg.smart_routing.enabled
            else "disabled"
        )
        typer.echo(
            f"{cfg.name:<16} {cfg.model_config.backend:<16} "
            f"{cfg.model_config.model:<34} {sr_status}"
        )


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


@app.command()
def validate(
    name: str = typer.Argument(..., help="Agent name to validate."),
    agents_dir: Path = _AGENTS_DIR_OPT,
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

    if (
        cfg.smart_routing
        and cfg.smart_routing.enabled
        and cfg.model_config.backend != "anthropic-sdk"
    ):
        backend = cfg.model_config.backend
        typer.echo(
            f"Warning: smart_routing.enabled=true but backend={backend!r} "
            "— smart routing will be ignored at runtime"
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point."""
    app()
