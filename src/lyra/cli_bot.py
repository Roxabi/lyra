"""lyra bot — CLI commands for managing bot credentials as Podman secrets."""

from __future__ import annotations

import os
import re
import subprocess
from typing import Optional

import typer

bot_app = typer.Typer(
    name="bot", help="Manage bot credentials stored as Podman secrets."
)

secret_app = typer.Typer(help="Manage bot credentials as Podman secrets.")
bot_app.add_typer(secret_app, name="secret")

_BOT_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _validate_bot_id(bot_id: str) -> None:
    if not _BOT_ID_RE.match(bot_id):
        typer.echo(
            f"invalid bot_id {bot_id!r}: must match {_BOT_ID_RE.pattern} "
            "(alphanumeric, hyphen, underscore only)",
            err=True,
        )
        raise typer.Exit(2)


def _podman_secret_create(name: str, content: bytes) -> None:
    subprocess.run(
        ["podman", "secret", "create", "--replace", name],
        input=content,
        check=True,
    )


@secret_app.command("install")
def install(
    platform: str,
    bot_id: str,
    from_env: Optional[str] = typer.Option(None, "--from-env"),
    webhook_from_env: Optional[str] = typer.Option(None, "--webhook-from-env"),
) -> None:
    """Create or replace a Podman secret holding a bot token."""
    _validate_bot_id(bot_id)
    token = (
        os.environ[from_env].encode()
        if from_env
        else typer.prompt("Token", hide_input=True).encode()
    )
    _podman_secret_create(f"lyra-bot-{platform}-{bot_id}", token)
    if webhook_from_env:
        webhook = os.environ[webhook_from_env].encode()
        _podman_secret_create(f"lyra-bot-{platform}-{bot_id}-webhook", webhook)


@secret_app.command("rm")
def rm(platform: str, bot_id: str) -> None:
    """Remove a bot's Podman secret (token + optional webhook)."""
    _validate_bot_id(bot_id)
    subprocess.run(
        ["podman", "secret", "rm", f"lyra-bot-{platform}-{bot_id}"],
        check=True,
    )
    subprocess.run(
        ["podman", "secret", "rm", f"lyra-bot-{platform}-{bot_id}-webhook"],
        check=False,
    )


@secret_app.command("list")
def list_() -> None:
    """List provisioned bot secrets (lyra-bot-* prefix)."""
    result = subprocess.run(
        ["podman", "secret", "ls", "--filter", "name=lyra-bot-", "--format", "json"],
        check=True,
        capture_output=True,
    )
    typer.echo(result.stdout)
