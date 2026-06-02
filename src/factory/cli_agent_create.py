"""lyra agent create — interactive agent creation command."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Optional

import click
import tomli_w
import typer

from factory.cli_agent import _AGENTS_DIR_OPT, _connect_store, _parse_tools, agent_app
from factory.paths import factory_data_dir


def _user_agents_dir() -> Path:
    """Resolve user agents dir from ROXABI_FACTORY_DIR at call time."""
    return factory_data_dir().resolve() / "agents"


_SYSTEM_AGENTS_DIR = Path(__file__).resolve().parent / "agents"
AGENTS_DIR = _SYSTEM_AGENTS_DIR

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _prompt_location() -> Path:
    typer.echo("  [u] user   — ~/.roxabi/factory/agents/      (personal, gitignored)")
    typer.echo(f"  [s] system — {AGENTS_DIR}  (versioned)")
    choice = typer.prompt("Save to", default="u", show_default=True)
    if choice.lower().startswith("s"):
        return _SYSTEM_AGENTS_DIR
    return _user_agents_dir()


def _prompt_sr_subconfig() -> tuple[bool, int | None, list[str], dict[str, str]]:
    sr_user = typer.confirm("Enable smart routing?", default=False)
    if not sr_user:
        return False, None, [], {}
    typer.echo(
        "Warning: smart_routing is no longer supported on any backend — "
        "forcing smart_routing.enabled to false."
    )
    return False, None, [], {}


def _build_toml(  # noqa: PLR0913 — DEBT:wiring-bootstrap-deps
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

_VALID_EFFORT_VALUES = frozenset({"low", "medium", "high", "xhigh", "max", "none"})


@agent_app.command()  # noqa: C901 — DEBT:complexity-residual
def create(
    name: Optional[str] = typer.Argument(
        None,
        help="Agent name (non-interactive mode when provided with --backend/--model).",
    ),
    backend: Optional[str] = typer.Option(
        None,
        "--backend",
        help="Backend: claude-cli (non-interactive mode).",
    ),
    model: Optional[str] = typer.Option(
        None,
        "--model",
        help="Model identifier (non-interactive mode).",
    ),
    effort: str = typer.Option(
        "medium",
        "--effort",
        help="Extended-thinking effort: low|medium|high|xhigh|max|none.",
    ),
    agents_dir: Optional[Path] = _AGENTS_DIR_OPT,
) -> None:
    """Create a new agent (TOML wizard, or non-interactive with --backend/--model)."""
    # Non-interactive path: name + --backend + --model all provided.
    if name is not None and backend is not None and model is not None:
        _create_noninteractive(name=name, backend=backend, model=model, effort=effort)
        return

    # Interactive TOML wizard (existing behavior).
    _create_interactive(
        name_arg=name,
        effort_arg=effort,
        agents_dir=agents_dir,
    )


def _create_noninteractive(name: str, backend: str, model: str, effort: str) -> None:
    """Create an agent directly in DB (non-interactive, no TOML file)."""
    from factory.core.agent.agent_models import AgentRow

    if not re.match(r"^[a-zA-Z0-9_-]+$", name):
        typer.echo(f"Error: invalid agent name {name!r} — only [a-zA-Z0-9_-] allowed")
        raise typer.Exit(1)
    effort_lower = effort.lower()
    if effort_lower not in _VALID_EFFORT_VALUES:
        typer.echo(
            f"Error: --effort must be one of {sorted(_VALID_EFFORT_VALUES)},"
            f" got {effort!r}"
        )
        raise typer.Exit(1)
    stored_effort: str | None = None if effort_lower == "none" else effort_lower

    async def _run() -> None:
        store = await _connect_store()
        try:
            if store.get(name) is not None:
                typer.echo(f"Error: agent {name!r} already exists in DB", err=True)
                raise typer.Exit(1)
            row = AgentRow(
                name=name,
                backend=backend,
                model=model,
                effort=stored_effort,
                source="db",
            )
            await store.upsert(row)
            typer.echo(
                f"Created agent {name!r} (backend={backend}, model={model}, "
                f"effort={stored_effort!r})"
            )
        finally:
            await store.close()

    asyncio.run(_run())


def _create_interactive(  # noqa: C901 — DEBT:complexity-residual
    name_arg: Optional[str],
    effort_arg: str,
    agents_dir: Optional[Path],
) -> None:
    """Interactive TOML wizard (legacy path)."""
    if name_arg is not None:
        name = name_arg
    else:
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

    backend = typer.prompt("Backend", type=click.Choice(["claude-cli"]))
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
    sr_enabled, sr_history, sr_high_cmds, sr_models = _prompt_sr_subconfig()
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
    typer.echo("  lyra agent init  # import into DB")
    typer.echo(f"  lyra agent validate {name}")
    typer.echo("  lyra agent list")
