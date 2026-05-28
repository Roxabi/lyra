"""lyra agent telegram — Telegram bot management CLI commands."""

from __future__ import annotations

import typer

from lyra.agent_cmd.platforms import _commands
from lyra.cli_agent import agent_app

telegram_app = typer.Typer(name="telegram", help="Manage Telegram bots.")
agent_app.add_typer(telegram_app, name="telegram")


@telegram_app.command(name="list")
def list_() -> None:
    """List all Telegram bots."""
    _commands._list("telegram")


@telegram_app.command(name="show")
def show(bot_id: str = typer.Argument(..., help="Bot ID to show.")) -> None:
    """Show full config for a Telegram bot."""
    _commands._show("telegram", bot_id)


@telegram_app.command(name="add")
def add(  # noqa: PLR0913
    bot_id: str = typer.Argument(..., help="Bot ID to add."),
    agent: str = typer.Option("", "--agent", help="Agent name."),
    webhook_enabled: bool = typer.Option(
        False, "--webhook-enabled", help="Enable webhook."
    ),
    default_trust: str = typer.Option(
        "blocked", "--default-trust", help="Default trust level."
    ),
    owner_users: str = typer.Option(
        "", "--owner-users", help="Comma-separated owner user IDs."
    ),
    trusted_users: str = typer.Option(
        "", "--trusted-users", help="Comma-separated trusted user IDs."
    ),
    trusted_roles: str = typer.Option(
        "", "--trusted-roles", help="Comma-separated trusted role IDs."
    ),
    auto_thread: bool = typer.Option(
        False, "--auto-thread", help="Enable auto thread."
    ),
    thread_hot_hours: int = typer.Option(
        24, "--thread-hot-hours", help="Thread hot hours."
    ),
) -> None:
    """Add a Telegram bot."""
    _owner_users = (
        [x.strip() for x in owner_users.split(",") if x.strip()] if owner_users else []
    )
    _trusted_users = (
        [x.strip() for x in trusted_users.split(",") if x.strip()]
        if trusted_users
        else []
    )
    _trusted_roles = (
        [x.strip() for x in trusted_roles.split(",") if x.strip()]
        if trusted_roles
        else []
    )
    _commands._add(
        "telegram",
        bot_id,
        agent,
        webhook_enabled,
        default_trust,
        _owner_users,
        _trusted_users,
        _trusted_roles,
        auto_thread,
        thread_hot_hours,
    )


@telegram_app.command(name="edit")
def edit(bot_id: str = typer.Argument(..., help="Bot ID to edit.")) -> None:
    """Interactively edit a Telegram bot."""
    _commands._edit("telegram", bot_id)


@telegram_app.command(name="patch")
def patch(  # noqa: PLR0913
    bot_id: str = typer.Argument(..., help="Bot ID to patch."),
    agent: str | None = typer.Option(None, "--agent", help="Set agent."),
    webhook_enabled: bool | None = typer.Option(
        None, "--webhook-enabled/--no-webhook-enabled", help="Set webhook enabled."
    ),
    default_trust: str | None = typer.Option(
        None, "--default-trust", help="Set default trust level."
    ),
    owner_users: str | None = typer.Option(
        None, "--owner-users", help="Comma-separated owner user IDs."
    ),
    trusted_users: str | None = typer.Option(
        None, "--trusted-users", help="Comma-separated trusted user IDs."
    ),
    trusted_roles: str | None = typer.Option(
        None, "--trusted-roles", help="Comma-separated trusted role IDs."
    ),
    auto_thread: bool | None = typer.Option(
        None, "--auto-thread/--no-auto-thread", help="Set auto thread."
    ),
    thread_hot_hours: int | None = typer.Option(
        None, "--thread-hot-hours", help="Set thread hot hours."
    ),
) -> None:
    """Patch a single field of a Telegram bot."""
    kwargs = {
        "agent": agent,
        "webhook_enabled": webhook_enabled,
        "default_trust": default_trust,
        "owner_users": (
            [x.strip() for x in owner_users.split(",") if x.strip()]
            if owner_users is not None
            else None
        ),
        "trusted_users": (
            [x.strip() for x in trusted_users.split(",") if x.strip()]
            if trusted_users is not None
            else None
        ),
        "trusted_roles": (
            [x.strip() for x in trusted_roles.split(",") if x.strip()]
            if trusted_roles is not None
            else None
        ),
        "auto_thread": auto_thread,
        "thread_hot_hours": thread_hot_hours,
    }
    _commands._patch("telegram", bot_id, **kwargs)


@telegram_app.command(name="remove")
def remove(
    bot_id: str = typer.Argument(..., help="Bot ID to remove."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Remove a Telegram bot."""
    _commands._remove("telegram", bot_id, yes)


@telegram_app.command(name="assign")
def assign(
    bot_id: str = typer.Argument(..., help="Bot ID to assign."),
    agent: str = typer.Option(..., "--agent", help="Agent name to assign."),
) -> None:
    """Assign an agent to a Telegram bot."""
    _commands._assign("telegram", bot_id, agent)


@telegram_app.command(name="unassign")
def unassign(bot_id: str = typer.Argument(..., help="Bot ID to unassign.")) -> None:
    """Unassign the agent from a Telegram bot."""
    _commands._unassign("telegram", bot_id)


@telegram_app.command(name="validate")
def validate(bot_id: str = typer.Argument(..., help="Bot ID to validate.")) -> None:
    """Validate a Telegram bot configuration."""
    _commands._validate("telegram", bot_id)
