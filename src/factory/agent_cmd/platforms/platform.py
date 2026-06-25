"""Parameterized platform bot management CLI — single source for all platforms."""

from __future__ import annotations

import typer

from factory.agent_cmd.platforms import _commands
from factory.cli.agent import agent_app


def make_platform_app(platform: str) -> typer.Typer:
    """Create and register a Typer sub-app for *platform* on ``agent_app``.

    All command logic is delegated to :mod:`factory.agent_cmd.platforms._commands`;
    this function only wires the Typer CLI surface.
    """
    platform_app = typer.Typer(
        name=platform, help=f"Manage {platform.capitalize()} bots."
    )
    agent_app.add_typer(platform_app, name=platform)

    @platform_app.command(name="list")
    def list_() -> None:
        _commands._list(platform)

    list_.__doc__ = f"List all {platform.capitalize()} bots."

    @platform_app.command(name="show")
    def show(bot_id: str = typer.Argument(..., help="Bot ID to show.")) -> None:
        _commands._show(platform, bot_id)

    show.__doc__ = f"Show full config for a {platform.capitalize()} bot."

    @platform_app.command(name="add")
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
        public_bot: str = typer.Option(
            "",
            "--public-bot",
            help="Handle of the dedicated public bot for ADR-090 §5 deny "
            "refusals (e.g. @bot_public). Empty → generic factory.roxabi.dev "
            "pointer. Not an authorization grant.",
        ),
    ) -> None:
        _owner_users = (
            [x.strip() for x in owner_users.split(",") if x.strip()]
            if owner_users
            else []
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
            platform,
            bot_id,
            agent,
            webhook_enabled,
            default_trust,
            _owner_users,
            _trusted_users,
            _trusted_roles,
            auto_thread,
            thread_hot_hours,
            public_bot or None,
        )

    add.__doc__ = f"Add a {platform.capitalize()} bot."

    @platform_app.command(name="edit")
    def edit(bot_id: str = typer.Argument(..., help="Bot ID to edit.")) -> None:
        _commands._edit(platform, bot_id)

    edit.__doc__ = f"Interactively edit a {platform.capitalize()} bot."

    @platform_app.command(name="patch")
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
        _commands._patch(platform, bot_id, **kwargs)

    patch.__doc__ = f"Patch a single field of a {platform.capitalize()} bot."

    @platform_app.command(name="remove")
    def remove(
        bot_id: str = typer.Argument(..., help="Bot ID to remove."),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
    ) -> None:
        _commands._remove(platform, bot_id, yes)

    remove.__doc__ = f"Remove a {platform.capitalize()} bot."

    @platform_app.command(name="assign")
    def assign(
        bot_id: str = typer.Argument(..., help="Bot ID to assign."),
        agent: str = typer.Option(..., "--agent", help="Agent name to assign."),
    ) -> None:
        _commands._assign(platform, bot_id, agent)

    assign.__doc__ = f"Assign an agent to a {platform.capitalize()} bot."

    @platform_app.command(name="unassign")
    def unassign(
        bot_id: str = typer.Argument(..., help="Bot ID to unassign."),
    ) -> None:
        _commands._unassign(platform, bot_id)

    unassign.__doc__ = f"Unassign the agent from a {platform.capitalize()} bot."

    @platform_app.command(name="validate")
    def validate(
        bot_id: str = typer.Argument(..., help="Bot ID to validate."),
    ) -> None:
        _commands._validate(platform, bot_id)

    validate.__doc__ = f"Validate a {platform.capitalize()} bot configuration."

    return platform_app
