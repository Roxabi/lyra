"""Shared helpers for platform-specific bot CLI commands."""

from __future__ import annotations

import re

import typer

from lyra.core.agent.bot_models import BotRow

_BOT_ID_RE = re.compile(r"[A-Za-z0-9_-]+")


def _validate_bot_id(bot_id: str) -> None:
    if not _BOT_ID_RE.fullmatch(bot_id):
        typer.echo(
            f"invalid bot_id {bot_id!r}: must match [A-Za-z0-9_-]+ "
            "(alphanumeric, hyphen, underscore only)",
            err=True,
        )
        raise typer.Exit(2)


def _format_table(rows: list[BotRow]) -> None:
    """Print bot rows as a formatted table."""
    typer.echo(
        f"{'BOT_ID':<18} {'AGENT':<18} {'TRUST':<10} "
        f"{'WEBHOOK':<8} {'AUTO_THREAD':<12} {'OWNERS':<30}"
    )
    for row in sorted(rows, key=lambda r: r.bot_id):
        owners = ", ".join(row.owner_users) if row.owner_users else "-"
        typer.echo(
            f"{row.bot_id:<18} {row.agent:<18} {row.default_trust:<10} "
            f"{'yes' if row.webhook_enabled else 'no':<8} "
            f"{'yes' if row.auto_thread else 'no':<12} {owners:<30}"
        )


def _prompt_edit_string(field: str, current: str) -> str | None:
    val = typer.prompt(
        f"  {field} (current: {current!r}, blank=keep, -=clear)", default=""
    )
    v = val.strip()
    if v == "-":
        return ""
    return v if v else None


def _prompt_edit_bool(field: str, current: bool) -> bool | None:
    val = typer.prompt(
        f"  {field} (current: {current}, blank=keep, y/n)", default=""
    )
    v = val.strip().lower()
    if v == "y":
        return True
    elif v == "n":
        return False
    return None


def _prompt_edit_list(field: str, current: list[str]) -> list[str] | None:
    val = typer.prompt(
        f"  {field} (current: {current}, blank=keep, -=clear)", default=""
    )
    v = val.strip()
    if v == "-":
        return []
    elif v:
        return [x.strip() for x in v.split(",") if x.strip()]
    return None
