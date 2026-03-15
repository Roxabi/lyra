"""lyra agent — agent management CLI commands (mounted by cli.py)."""

from __future__ import annotations

import asyncio
import dataclasses
import json as _json
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
from lyra.core.agent_store import AgentStore

agent_app = typer.Typer(name="agent", help="Manage agent configurations.")
_DEFAULT_TOOLS = ["Read", "Grep", "Glob", "WebFetch", "WebSearch"]
_AGENTS_DIR_OPT: Optional[Path] = typer.Option(
    None, help="Directory where agent TOMLs live (skips location prompt)."
)


def _get_db_path() -> Path:
    return (
        Path(os.environ.get("LYRA_VAULT_DIR", str(Path.home() / ".lyra"))) / "auth.db"
    )


async def _connect_store() -> AgentStore:
    store = AgentStore(db_path=_get_db_path())
    await store.connect()
    return store


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


@agent_app.command(name="init")
def init_agents(
    force: bool = typer.Option(False, "--force", help="Overwrite existing rows."),
    agents_dir: Optional[Path] = _AGENTS_DIR_OPT,
) -> None:
    """Seed the agent DB from existing TOML files (one-time migration)."""

    async def _run() -> None:
        store = await _connect_store()
        try:
            dirs = (
                [agents_dir] if agents_dir else [_USER_AGENTS_DIR, _SYSTEM_AGENTS_DIR]
            )
            imported = skipped = errors = 0
            for d in dirs:
                if not d.exists():
                    continue
                for toml_file in sorted(d.glob("*.toml")):
                    try:
                        n = await store.seed_from_toml(toml_file, force=force)
                        if n:
                            imported += 1
                            typer.echo(f"  imported: {toml_file.name}")
                        else:
                            skipped += 1
                    except Exception as e:
                        typer.echo(f"  error: {toml_file.name}: {e}", err=True)
                        errors += 1
            typer.echo(
                f"\nDone: {imported} imported, {skipped} skipped, {errors} errors"
            )
        finally:
            await store.close()

    asyncio.run(_run())


@agent_app.command(name="list")
def list_agents(
    agents_dir: Optional[Path] = _AGENTS_DIR_OPT,
) -> None:
    """List all configured agents (from DB, or TOML files if --agents-dir given)."""
    if agents_dir is not None:
        _list_from_dir(agents_dir, source_label=None)
        return

    async def _run() -> None:
        store = await _connect_store()
        try:
            rows = store.get_all()
            states = await store.get_all_runtime_states()
            bot_map: dict[str, list[str]] = {}
            for (platform, bot_id), agent_name in store._bot_map.items():
                bot_map.setdefault(agent_name, []).append(f"{platform}:{bot_id}")

            typer.echo(
                f"{'NAME':<18} {'BACKEND':<14} {'MODEL':<32} {'STATUS':<8} SOURCE  BOTS"
            )
            for row in sorted(rows, key=lambda r: r.name):
                state = states.get(row.name)
                status = state.status if state else "idle"
                bots = ", ".join(bot_map.get(row.name, [])) or "—"
                typer.echo(
                    f"{row.name:<18} {row.backend:<14} {row.model:<32} "
                    f"{status:<8} {row.source:<7} {bots}"
                )
            if not rows:
                typer.echo("  (no agents in DB — run 'lyra agent init' to import)")
        finally:
            await store.close()

    asyncio.run(_run())


@agent_app.command(name="show")
def show(name: str = typer.Argument(..., help="Agent name to show.")) -> None:
    """Print full config for one agent from DB."""

    async def _run() -> None:
        store = await _connect_store()
        try:
            row = store.get(name)
            if row is None:
                typer.echo(f"Error: agent {name!r} not found in DB", err=True)
                raise typer.Exit(1)
            for f in dataclasses.fields(row):
                typer.echo(f"  {f.name:<22} {getattr(row, f.name)!r}")
        finally:
            await store.close()

    asyncio.run(_run())


@agent_app.command()
def validate(  # noqa: C901, PLR0915
    name: str = typer.Argument(..., help="Agent name to validate."),
    agents_dir: Optional[Path] = _AGENTS_DIR_OPT,
) -> None:
    """Validate an agent config from DB (or TOML if --agents-dir given)."""
    if agents_dir is not None:
        # Legacy TOML validation path
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
                typer.echo(
                    "smart_routing: enabled (anthropic-sdk constraint satisfied)"
                )
        return

    async def _run() -> None:  # noqa: C901
        store = await _connect_store()
        try:
            row = store.get(name)
            if row is None:
                typer.echo(f"Error: agent {name!r} not found in DB", err=True)
                raise typer.Exit(1)
            errors_found: list[str] = []
            if row.backend not in ("claude-cli", "anthropic-sdk", "ollama"):
                errors_found.append(f"unknown backend: {row.backend!r}")
            if not row.model:
                errors_found.append("model is empty")
            if row.smart_routing_json:
                try:
                    sr = _json.loads(row.smart_routing_json)
                    if sr.get("enabled") and row.backend != "anthropic-sdk":
                        errors_found.append(
                            f"smart_routing.enabled=true but backend={row.backend!r} "
                            "(requires anthropic-sdk)"
                        )
                except Exception:
                    sr_val = row.smart_routing_json
                    errors_found.append(
                        f"smart_routing_json is invalid JSON: {sr_val!r}"
                    )
            for field_name in ("tools_json", "plugins_json"):
                val = getattr(row, field_name)
                try:
                    parsed = _json.loads(val)
                    if not isinstance(parsed, list):
                        raise ValueError("not a list")
                except Exception:
                    errors_found.append(
                        f"{field_name} is not a valid JSON array: {val!r}"
                    )
            if errors_found:
                for e in errors_found:
                    typer.echo(f"Error: {e}", err=True)
                raise typer.Exit(1)
            typer.echo(f"agent {name!r}: OK")
        finally:
            await store.close()

    asyncio.run(_run())


@agent_app.command(name="edit")
def edit(name: str = typer.Argument(..., help="Agent name to edit.")) -> None:
    """Interactively edit an agent config in DB (blank input = keep current value)."""

    async def _run() -> None:
        store = await _connect_store()
        try:
            row = store.get(name)
            if row is None:
                typer.echo(f"Error: agent {name!r} not found in DB", err=True)
                raise typer.Exit(1)
            editable_fields = [
                "backend",
                "model",
                "max_turns",
                "persona",
                "show_intermediate",
                "cwd",
                "memory_namespace",
            ]
            new_vals: dict = {}
            for field_name in editable_fields:
                current = getattr(row, field_name)
                val = typer.prompt(
                    f"  {field_name} (current: {current!r}, blank=keep)", default=""
                )
                if val.strip():
                    # Coerce types
                    if field_name == "max_turns":
                        new_vals[field_name] = int(val.strip())
                    elif field_name == "show_intermediate":
                        new_vals[field_name] = val.strip().lower() in (
                            "true",
                            "1",
                            "yes",
                        )
                    else:
                        new_vals[field_name] = val.strip()
            if not new_vals:
                typer.echo("No changes.")
                return
            updated = dataclasses.replace(row, **new_vals)
            await store.upsert(updated)
            typer.echo(f"Updated: {', '.join(new_vals.keys())}")
        finally:
            await store.close()

    asyncio.run(_run())


@agent_app.command(name="delete")
def delete_agent(
    name: str = typer.Argument(..., help="Agent name to delete."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Delete an agent from DB. Refuses if any bot is assigned to it."""

    async def _run() -> None:
        store = await _connect_store()
        try:
            if store.get(name) is None:
                typer.echo(f"Error: agent {name!r} not found in DB", err=True)
                raise typer.Exit(1)
            if not yes:
                typer.confirm(f"Delete agent {name!r}?", abort=True)
            await store.delete(name)
            typer.echo(f"Deleted agent {name!r}")
        except ValueError as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1)
        finally:
            await store.close()

    asyncio.run(_run())


@agent_app.command(name="assign")
def assign(
    agent_name: str = typer.Argument(..., help="Agent name to assign."),
    bot: str = typer.Option(..., "--bot", help="Bot ID."),
    platform: str = typer.Option(
        ..., "--platform", help="Platform: telegram or discord."
    ),
) -> None:
    """Assign a bot to an agent (takes effect on next pool creation)."""
    if platform not in ("telegram", "discord"):
        typer.echo("Error: --platform must be 'telegram' or 'discord'", err=True)
        raise typer.Exit(1)

    async def _run() -> None:
        store = await _connect_store()
        try:
            if store.get(agent_name) is None:
                typer.echo(f"Error: agent {agent_name!r} not found in DB", err=True)
                raise typer.Exit(1)
            await store.set_bot_agent(platform, bot, agent_name)
            typer.echo(f"Assigned {platform}:{bot} → {agent_name}")
        finally:
            await store.close()

    asyncio.run(_run())


@agent_app.command(name="unassign")
def unassign(
    bot: str = typer.Option(..., "--bot", help="Bot ID."),
    platform: str = typer.Option(
        ..., "--platform", help="Platform: telegram or discord."
    ),
) -> None:
    """Remove a bot↔agent mapping (no-op if mapping doesn't exist)."""

    async def _run() -> None:
        store = await _connect_store()
        try:
            await store.remove_bot_agent(platform, bot)
            typer.echo(f"Unassigned {platform}:{bot}")
        finally:
            await store.close()

    asyncio.run(_run())
