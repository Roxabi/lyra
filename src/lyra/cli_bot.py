"""lyra bot — CLI commands for managing encrypted bot credentials."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Optional

import typer

from lyra.infrastructure.stores.credential_store import CredentialStore, LyraKeyring


def _vault_dir() -> Path:
    return Path(os.environ.get("LYRA_VAULT_DIR", str(Path.home() / ".lyra")))


bot_app = typer.Typer(
    name="bot", help="Manage bot credentials stored in ~/.lyra/config.db."
)


async def _make_store(vault: Path) -> CredentialStore:
    vault.mkdir(parents=True, exist_ok=True)
    keyring = LyraKeyring.load_or_create(vault / "keyring.key")
    store = CredentialStore(db_path=vault / "config.db", keyring=keyring)
    await store.connect()
    return store


@bot_app.command("add")
def bot_add(
    platform: str = typer.Option(..., help="Platform: telegram or discord"),
    bot_id: str = typer.Option(..., help="Bot ID as defined in config.toml"),
    token: str = typer.Option(..., help="Bot token", hide_input=True, prompt=True),
    webhook_secret: Optional[str] = typer.Option(
        None, help="Webhook secret (Telegram only)", hide_input=True
    ),
) -> None:
    """Store encrypted bot credentials in ~/.lyra/auth.db."""
    if platform not in ("telegram", "discord"):
        typer.echo(
            f"✗ Unknown platform '{platform}'. Use: telegram or discord", err=True
        )
        raise typer.Exit(1)
    asyncio.run(_bot_add_async(platform, bot_id, token, webhook_secret))


async def _bot_add_async(
    platform: str,
    bot_id: str,
    token: str,
    webhook_secret: Optional[str],
) -> None:
    vault = _vault_dir()
    store = await _make_store(vault)
    try:
        if await store.exists(platform, bot_id):
            typer.confirm(
                f"Credentials already exist for {platform}/{bot_id}. Overwrite?",
                abort=True,
            )
        await store.set(platform, bot_id, token, webhook_secret)
        typer.echo(f"✓ Credentials stored for {platform}/{bot_id}")
    finally:
        await store.close()


@bot_app.command("list")
def bot_list() -> None:
    """List all stored bot credentials (tokens masked)."""
    asyncio.run(_bot_list_async())


async def _bot_list_async() -> None:
    vault = _vault_dir()
    store = await _make_store(vault)
    try:
        rows = await store.list_all()
        if not rows:
            typer.echo("No credentials stored. Run lyra bot add to get started.")
            return
        typer.echo(f"{'PLATFORM':<12} {'BOT ID':<20} {'TOKEN':<20} UPDATED")
        typer.echo("-" * 70)
        for row in rows:
            raw_token = await store.get(row.platform, row.bot_id)
            if raw_token:
                masked = f"***...{raw_token[-4:]}"
            else:
                masked = "***"
            typer.echo(
                f"{row.platform:<12} {row.bot_id:<20} {masked:<20} {row.updated_at}"
            )
    finally:
        await store.close()


@bot_app.command("remove")
def bot_remove(
    platform: str = typer.Option(...),
    bot_id: str = typer.Option(...),
) -> None:
    """Remove stored bot credentials."""
    typer.confirm(f"Remove credentials for {platform}/{bot_id}?", abort=True)
    asyncio.run(_bot_remove_async(platform, bot_id))


async def _bot_remove_async(platform: str, bot_id: str) -> None:
    vault = _vault_dir()
    store = await _make_store(vault)
    try:
        deleted = await store.delete(platform, bot_id)
        if deleted:
            typer.echo(f"✓ Removed credentials for {platform}/{bot_id}")
        else:
            typer.echo(f"✗ Not found for {platform}/{bot_id}")
    finally:
        await store.close()
