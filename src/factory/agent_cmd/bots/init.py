"""factory bot init command — seed bot configurations from config.toml."""

from __future__ import annotations

import asyncio
import os
import re
import sqlite3
import tomllib
from pathlib import Path
from typing import Any

import typer
from pydantic import BaseModel, ConfigDict, ValidationError

from factory.cli.bot import _connect_bot_store, _maybe_publish_roster, bot_app
from factory.core.agent.bot_models import (
    DEFAULT_AUTO_THREAD,
    DEFAULT_THREAD_HOT_HOURS,
    BotRow,
)
from factory.paths import factory_data_dir


class _BotSeedEntry(BaseModel):
    """Validates one raw TOML bot entry; rejects unknown keys (G18 typo trap)."""

    model_config = ConfigDict(extra="forbid")

    bot_id: str = "main"
    agent: str = "lyra_default"
    webhook_enabled: bool = False
    auto_thread: bool = DEFAULT_AUTO_THREAD
    thread_hot_hours: int = DEFAULT_THREAD_HOT_HOURS
    public_bot: str | None = None
    # Credentials resolved from secrets at runtime — not stored in BotRow.
    token: str | None = None
    webhook_secret: str | None = None
    bot_username: str | None = None


def _find_config_toml() -> Path | None:
    """Resolve config.toml path using standard resolution."""
    env_path = os.environ.get("FACTORY_CONFIG")
    if env_path:
        p = Path(env_path).expanduser().resolve()
        if p.exists():
            return p
    vault_dir = factory_data_dir()
    for candidate in [vault_dir / "config.toml", Path("config.toml")]:
        if candidate.exists():
            return candidate
    return None


@bot_app.command(name="init")
def init_bots(
    force: bool = typer.Option(False, "--force", help="Overwrite existing rows."),
) -> None:
    """Seed the bot DB from config.toml (one-time migration)."""

    async def _run() -> None:
        store = await _connect_bot_store()
        try:
            config_path = _find_config_toml()
            if config_path is None:
                typer.echo("No config.toml found.", err=True)
                raise typer.Exit(1)

            try:
                with config_path.open("rb") as f:
                    raw = tomllib.load(f)
            except (tomllib.TOMLDecodeError, OSError) as e:
                typer.echo(f"Error parsing {config_path}: {e}", err=True)
                raise typer.Exit(1)

            bots, errors = _merge_bots(raw)

            seeded = skipped = 0
            for row in bots:
                try:
                    existing = store.get(row.platform, row.bot_id)
                    if existing is not None and not force:
                        skipped += 1
                        typer.echo(f"  skipped: {row.platform}/{row.bot_id}")
                        continue
                    await store.upsert(row)
                    seeded += 1
                    typer.echo(f"  seeded: {row.platform}/{row.bot_id}")
                except (sqlite3.Error, RuntimeError) as e:
                    typer.echo(f"  error: {row.platform}/{row.bot_id}: {e}", err=True)
                    errors += 1

            if store.get_all():
                await _maybe_publish_roster(store)

            typer.echo(f"\nDone: {seeded} seeded, {skipped} skipped, {errors} errors")
            if errors:
                raise typer.Exit(1)
        finally:
            await store.close()

    asyncio.run(_run())


_BOT_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_PLATFORM_RE = re.compile(r"^(telegram|discord)$")


def _merge_bots(raw: dict[str, Any]) -> tuple[list[BotRow], int]:  # noqa: C901 — merge helper
    """Merge ``[[telegram.bots]]`` and ``[[discord.bots]]`` entries per bot."""
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    validation_errors = 0

    def _add_entries(section_path: tuple[str, ...], platform: str) -> None:
        nonlocal validation_errors
        section: Any = raw
        for key in section_path[:-1]:
            section = section.get(key, {})
            if not isinstance(section, dict):
                return
        section = section.get(section_path[-1], [])
        if not isinstance(section, list):
            return
        for entry in section:
            if not isinstance(entry, dict):
                continue
            bot_id = entry.get("bot_id", "main")
            if not _BOT_ID_RE.match(bot_id) or not _PLATFORM_RE.match(platform):
                typer.echo(
                    f"  error: invalid platform/bot_id ({platform}/{bot_id}) — skipped",
                    err=True,
                )
                validation_errors += 1
                continue
            try:
                _BotSeedEntry.model_validate(entry)
            except ValidationError as exc:
                typer.echo(
                    f"  error: invalid bot entry ({platform}/{bot_id}): {exc}",
                    err=True,
                )
                validation_errors += 1
                continue
            key = (platform, bot_id)
            merged.setdefault(key, {"platform": platform, "bot_id": bot_id})
            for k, v in entry.items():
                if k == "bot_id":
                    continue
                merged[key][k] = v

    _add_entries(("telegram", "bots"), "telegram")
    _add_entries(("discord", "bots"), "discord")

    rows: list[BotRow] = []
    for (platform, bot_id), data in sorted(merged.items()):
        rows.append(
            BotRow(
                platform=platform,
                bot_id=bot_id,
                agent=data.get("agent", "lyra_default"),
                webhook_enabled=data.get("webhook_enabled", False),
                auto_thread=data.get("auto_thread", DEFAULT_AUTO_THREAD),
                thread_hot_hours=data.get("thread_hot_hours", DEFAULT_THREAD_HOT_HOURS),
                public_bot=data.get("public_bot"),
            )
        )
    return rows, validation_errors