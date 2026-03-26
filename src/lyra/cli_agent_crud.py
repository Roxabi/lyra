"""lyra agent CRUD — init, list, show, validate, edit, delete, assign, unassign."""

from __future__ import annotations

import asyncio
import dataclasses
import json as _json
import tomllib
from pathlib import Path

import typer

from lyra.cli_agent import _AGENTS_DIR_OPT, _connect_store, _list_from_dir, agent_app
from lyra.core.agent_config import _VALID_BACKENDS

# Agent TOML directories for seeding
_USER_AGENTS_DIR = Path.home() / ".lyra" / "agents"
_SYSTEM_AGENTS_DIR = Path(__file__).resolve().parent / "agents"


@agent_app.command(name="init")
def init_agents(
    force: bool = typer.Option(False, "--force", help="Overwrite existing rows."),
    agents_dir: Path | None = _AGENTS_DIR_OPT,
) -> None:
    """Seed the agent DB from existing TOML files (one-time migration)."""

    async def _run() -> None:
        store = await _connect_store()
        try:
            sd = [agents_dir] if agents_dir else [_USER_AGENTS_DIR, _SYSTEM_AGENTS_DIR]
            imported = skipped = errors = 0
            for d in sd:
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
def list_agents(agents_dir: Path | None = _AGENTS_DIR_OPT) -> None:
    """List all configured agents (DB by default, or TOML files if --agents-dir)."""
    if agents_dir is not None:
        _list_from_dir(agents_dir, source_label=None)
        return

    async def _run() -> None:
        store = await _connect_store()
        try:
            rows = store.get_all()
            states = await store.get_all_runtime_states()
            bot_map: dict[str, list[str]] = {}
            for (platform, bot_id), agent_name in store.get_all_bot_mappings().items():
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
    agents_dir: Path | None = _AGENTS_DIR_OPT,
) -> None:
    """Validate an agent config from DB."""

    async def _run() -> None:  # noqa: C901
        store = await _connect_store()
        try:
            row = store.get(name)
            if row is None:
                typer.echo(f"Error: agent {name!r} not found in DB", err=True)
                raise typer.Exit(1)
            errors_found: list[str] = []
            if row.backend not in _VALID_BACKENDS:
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
                    errors_found.append(
                        f"smart_routing_json invalid JSON: {row.smart_routing_json!r}"
                    )
            for fn in ("tools_json", "plugins_json", "permissions_json"):
                val = getattr(row, fn)
                try:
                    if not isinstance(_json.loads(val), list):
                        raise ValueError("not a list")
                except Exception:
                    errors_found.append(f"{fn} is not a valid JSON array: {val!r}")
            for fn in ("workspaces_json", "commands_json"):
                val = getattr(row, fn)
                if val is not None:
                    try:
                        if not isinstance(_json.loads(val), dict):
                            raise ValueError("not an object")
                    except Exception:
                        errors_found.append(f"{fn} is not a valid JSON object: {val!r}")
            if errors_found:
                for e in errors_found:
                    typer.echo(f"Error: {e}", err=True)
                raise typer.Exit(1)
            typer.echo(f"agent {name!r}: OK")
        finally:
            await store.close()

    asyncio.run(_run())


_TTS_EDIT_FIELDS = (
    "engine",
    "voice",
    "language",
    "accent",
    "personality",
    "speed",
    "emotion",
    "exaggeration",
    "cfg_weight",
)

_TTS_FLOAT_FIELDS = {"exaggeration", "cfg_weight"}


def _edit_tts_section(tts_data: dict | None) -> dict | None:
    """Interactively edit TTS fields.  Returns updated dict or None."""
    initialized = False
    if tts_data is None:
        init = typer.prompt(
            "  Initialize TTS config for this agent? [y/N]", default="N"
        )
        if init.strip().lower() not in ("y", "yes"):
            return None
        tts_data = {}
        initialized = True

    typer.echo("  --- TTS config ---")
    changed = False
    for fname in _TTS_EDIT_FIELDS:
        current = tts_data.get(fname)
        val = typer.prompt(
            f"  {fname} (current: {current!r}, blank=keep, '-'=clear)",
            default="",
        )
        v = val.strip()
        if not v:
            continue
        if v in ("-", "none"):
            tts_data.pop(fname, None)
            changed = True
            continue
        if fname in _TTS_FLOAT_FIELDS:
            try:
                tts_data[fname] = float(v)
            except ValueError:
                typer.echo(f"    Invalid float for {fname}: {v!r} — skipped")
                continue
        else:
            tts_data[fname] = v
        changed = True

    return tts_data if (changed or initialized) else None


@agent_app.command(name="edit")
def edit(name: str = typer.Argument(..., help="Agent name to edit.")) -> None:
    """Edit an agent config interactively (blank input = keep current value)."""

    async def _run() -> None:
        store = await _connect_store()
        try:
            row = store.get(name)
            if row is None:
                typer.echo(f"Error: agent {name!r} not found in DB", err=True)
                raise typer.Exit(1)
            editable = [
                "backend",
                "model",
                "max_turns",
                "show_intermediate",
                "cwd",
                "memory_namespace",
                "fallback_language",
            ]
            new_vals: dict = {}
            for field_name in editable:
                current = getattr(row, field_name)
                val = typer.prompt(
                    f"  {field_name} (current: {current!r}, blank=keep)", default=""
                )
                v = val.strip()
                if v:
                    if field_name == "max_turns":
                        new_vals[field_name] = int(v)
                    elif field_name == "show_intermediate":
                        new_vals[field_name] = v.lower() in ("true", "1", "yes")
                    else:
                        new_vals[field_name] = v

            # Voice editing sub-section
            existing_voice = _json.loads(row.voice_json) if row.voice_json else None
            existing_tts = existing_voice.get("tts") if existing_voice else None
            updated_tts = _edit_tts_section(existing_tts)
            if updated_tts is not None:
                voice_obj = existing_voice or {"tts": {}, "stt": {}}
                voice_obj["tts"] = updated_tts
                new_vals["voice_json"] = _json.dumps(voice_obj)

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
    """Delete an agent from DB (refuses if a bot is still assigned)."""

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
    """Remove a bot-agent mapping (no-op if mapping doesn't exist)."""

    async def _run() -> None:
        store = await _connect_store()
        try:
            await store.remove_bot_agent(platform, bot)
            typer.echo(f"Unassigned {platform}:{bot}")
        finally:
            await store.close()

    asyncio.run(_run())


@agent_app.command(name="patch")
def patch_agent(
    name: str = typer.Argument(..., help="Agent name to patch."),
    json_patch: str = typer.Option(
        ..., "--json", help="JSON object with field updates."
    ),
) -> None:
    """Apply a partial JSON patch to an agent in DB."""
    try:
        fields = _json.loads(json_patch)
    except _json.JSONDecodeError as e:
        typer.echo(f"Error: invalid JSON — {e}", err=True)
        raise typer.Exit(1)
    if not isinstance(fields, dict):
        typer.echo("Error: --json must be a JSON object (dict)", err=True)
        raise typer.Exit(1)

    async def _run() -> None:
        from lyra.core.agent_refiner import RefinementPatch

        store = await _connect_store()
        try:
            row = store.get(name)
            if row is None:
                typer.echo(f"Error: agent {name!r} not found in DB", err=True)
                raise typer.Exit(1)
            patch = RefinementPatch(fields=fields)
            updated = patch.to_agent_row(row)
            await store.upsert(updated)
            typer.echo(f"Patched {name!r}: {', '.join(fields.keys())}")
        finally:
            await store.close()

    asyncio.run(_run())


@agent_app.command(name="refine")
def refine(
    name: str = typer.Argument(..., help="Agent name to refine."),
) -> None:
    """Interactively refine an agent profile via LLM-guided session."""
    from lyra.core.agent_refiner import AgentRefiner, RefinementCancelled, TerminalIO

    async def _run() -> None:
        store = await _connect_store()
        try:
            if store.get(name) is None:
                typer.echo(f"Error: agent {name!r} not found in DB", err=True)
                raise typer.Exit(1)
            refiner = AgentRefiner(name, store)
            io = TerminalIO()
            before_row = store.get(name)
            try:
                patch = refiner.run_session(io)
            except RefinementCancelled:
                typer.echo("\nRefinement session cancelled.")
                raise typer.Exit(0)
            except RuntimeError as e:
                typer.echo(f"Error: {e}", err=True)
                raise typer.Exit(1)
            # Apply patch
            current = store.get(name)
            if current is None:
                typer.echo(
                    f"Error: agent {name!r} disappeared during session", err=True
                )
                raise typer.Exit(1)
            updated = patch.to_agent_row(current)
            await store.upsert(updated)
            typer.echo("\nChanged fields:")
            for field_name, new_val in patch.fields.items():
                old_val = getattr(before_row, field_name, "?")
                typer.echo(f"  {field_name}: {old_val!r} → {new_val!r}")
            typer.echo("\nAgent profile updated. Restart lyra to apply.")
        finally:
            await store.close()

    asyncio.run(_run())
