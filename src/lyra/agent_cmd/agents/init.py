"""lyra agent init/validate/delete commands — initialization and maintenance."""

from __future__ import annotations

import asyncio
import json as _json
import os
from pathlib import Path

import typer

from lyra.cli_agent import _AGENTS_DIR_OPT, _connect_store, agent_app
from lyra.core.agent_config import _VALID_BACKENDS


def _user_agents_dir() -> Path:
    """Resolve user agents dir from LYRA_VAULT_DIR at call time."""
    return (
        Path(os.environ.get("LYRA_VAULT_DIR", str(Path.home() / ".lyra"))).resolve()
        / "agents"
    )


_SYSTEM_AGENTS_DIR = Path(__file__).resolve().parent.parent.parent / "agents"


@agent_app.command(name="init")
def init_agents(
    force: bool = typer.Option(False, "--force", help="Overwrite existing rows."),
    agents_dir: Path | None = _AGENTS_DIR_OPT,
) -> None:
    """Seed the agent DB from existing TOML files (one-time migration)."""

    async def _run() -> None:
        store = await _connect_store()
        try:
            sd = (
                [agents_dir] if agents_dir else [_user_agents_dir(), _SYSTEM_AGENTS_DIR]
            )
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


# ---------------------------------------------------------------------------
# validate command
# ---------------------------------------------------------------------------


@agent_app.command()
def validate(  # noqa: C901 -- validation walks multiple config sections
    name: str = typer.Argument(..., help="Agent name to validate."),
    agents_dir: Path | None = _AGENTS_DIR_OPT,
) -> None:
    """Validate an agent config from DB."""

    async def _run() -> None:  # noqa: C901 -- mirrors validate() structure
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


# ---------------------------------------------------------------------------
# delete command
# ---------------------------------------------------------------------------


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
