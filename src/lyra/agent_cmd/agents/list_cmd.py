"""lyra agent list/show commands — read operations."""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path

import typer

from lyra.cli_agent import _AGENTS_DIR_OPT, _connect_store, _list_from_dir, agent_app


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
                bots = ", ".join(bot_map.get(row.name, [])) or "-"
                typer.echo(
                    f"{row.name:<18} {row.backend:<14} {row.model:<32} "
                    f"{status:<8} {row.source:<7} {bots}"
                )
            if not rows:
                typer.echo("  (no agents in DB - run 'lyra agent init' to import)")
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
