"""lyra agent — agent management CLI commands (mounted by cli.py).

Thin facade: defines ``agent_app`` and shared helpers, then imports
sub-modules that register their commands on ``agent_app``.
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import Optional

import typer

from lyra.core.stores.agent_store import AgentStore

agent_app = typer.Typer(name="agent", help="Manage agent configurations.")
_DEFAULT_TOOLS = ["Read", "Grep", "Glob", "WebFetch", "WebSearch"]
_AGENTS_DIR_OPT: Optional[Path] = typer.Option(
    None, help="Directory where agent TOMLs live (skips location prompt)."
)


# ---------------------------------------------------------------------------
# Shared helpers (used by cli_agent_create / cli_agent_crud)
# ---------------------------------------------------------------------------


def _get_db_path() -> Path:
    return (
        Path(os.environ.get("LYRA_VAULT_DIR", str(Path.home() / ".lyra"))) / "config.db"
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


def _list_from_dir(
    directory: Path,
    source_label: str | None,
    skip: set[str] | None = None,
) -> set[str]:
    """Print agents from a TOML directory (used by list and potentially others)."""
    import tomllib

    printed: set[str] = set()
    if not directory.exists():
        return printed
    for toml_file in sorted(directory.glob("*.toml")):
        agent_name = toml_file.stem
        if skip and agent_name in skip:
            continue
        try:
            with toml_file.open("rb") as f:
                data = tomllib.load(f)
            name = data.get("agent", {}).get("name", agent_name)
            backend = data.get("model", {}).get("backend", "?")
            model = data.get("model", {}).get("model", "?")
            sr = data.get("agent", {}).get("smart_routing", {})
            sr_status = "enabled" if sr.get("enabled") else "disabled"
        except Exception as e:
            typer.echo(f"  [warn] skipped {toml_file.name}: {e}", err=True)
            continue
        source = f"  {source_label}" if source_label else ""
        typer.echo(f"{name:<16} {backend:<16} {model:<34} {sr_status:<14}{source}")
        printed.add(agent_name)
    return printed


# ---------------------------------------------------------------------------
# Register commands from sub-modules (import triggers @agent_app.command())
# ---------------------------------------------------------------------------

importlib.import_module("lyra.cli_agent_create")  # noqa: E402 â registers commands via @agent_app.command()
importlib.import_module("lyra.cli_agent_crud")  # noqa: E402 â registers commands via @agent_app.command()
