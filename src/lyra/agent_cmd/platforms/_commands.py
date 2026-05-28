"""Platform-parameterized bot command handlers."""

from __future__ import annotations

import asyncio
import dataclasses
import subprocess
from typing import Any

import typer

from lyra.agent_cmd.platforms._shared import (
    _format_table,
    _prompt_edit_bool,
    _prompt_edit_list,
    _prompt_edit_string,
    _validate_bot_id,
)
from lyra.cli_agent import _connect_store as _connect_agent_store
from lyra.cli_bot import _connect_bot_store
from lyra.core.agent.bot_models import BotRow


def _list(platform: str) -> None:
    """List all bots for the given platform."""

    async def _run() -> None:
        store = await _connect_bot_store()
        try:
            rows = [r for r in store.get_all() if r.platform == platform]
            if not rows:
                typer.echo(f"  (no {platform} bots in DB)")
                return
            _format_table(rows)
        finally:
            await store.close()

    asyncio.run(_run())


def _show(platform: str, bot_id: str) -> None:
    """Show full config for one bot."""
    _validate_bot_id(bot_id)

    async def _run() -> None:
        store = await _connect_bot_store()
        try:
            row = store.get(platform, bot_id)
            if row is None:
                typer.echo(
                    f"Error: {platform} bot {bot_id!r} not found in DB", err=True
                )
                raise typer.Exit(1)
            for f in dataclasses.fields(row):
                typer.echo(f"  {f.name:<22} {getattr(row, f.name)!r}")
        finally:
            await store.close()

    asyncio.run(_run())


_VALID_TRUST = {"owner", "trusted", "public", "blocked"}


def _add(  # noqa: PLR0913
    platform: str,
    bot_id: str,
    agent: str = "",
    webhook_enabled: bool = False,
    default_trust: str = "blocked",
    owner_users: list[str] | None = None,
    trusted_users: list[str] | None = None,
    trusted_roles: list[str] | None = None,
    auto_thread: bool = False,
    thread_hot_hours: int = 24,
) -> None:
    """Create or replace a bot row."""
    _validate_bot_id(bot_id)
    if default_trust not in _VALID_TRUST:
        typer.echo(
            f"Error: invalid default_trust {default_trust!r}. "
            f"Must be one of: {', '.join(sorted(_VALID_TRUST))}",
            err=True,
        )
        raise typer.Exit(2)

    async def _run() -> None:
        store = await _connect_bot_store()
        try:
            row = BotRow(
                platform=platform,
                bot_id=bot_id,
                agent=agent,
                webhook_enabled=webhook_enabled,
                default_trust=default_trust,
                owner_users=list(owner_users or []),
                trusted_users=list(trusted_users or []),
                trusted_roles=list(trusted_roles or []),
                auto_thread=auto_thread,
                thread_hot_hours=thread_hot_hours,
            )
            await store.upsert(row)
            typer.echo(f"Added {platform}/{bot_id}")
        finally:
            await store.close()

    asyncio.run(_run())


def _edit(  # noqa: C901, PLR0915
    platform: str, bot_id: str
) -> None:
    """Interactively edit a bot (blank = keep, '-' = clear)."""
    _validate_bot_id(bot_id)

    async def _run() -> None:  # noqa: C901, PLR0915
        store = await _connect_bot_store()
        try:
            row = store.get(platform, bot_id)
            if row is None:
                typer.echo(
                    f"Error: {platform} bot {bot_id!r} not found in DB", err=True
                )
                raise typer.Exit(1)
            new_vals: dict[str, Any] = {}
            v = _prompt_edit_string("agent", row.agent)
            if v is not None:
                new_vals["agent"] = v
            v = _prompt_edit_bool("webhook_enabled", row.webhook_enabled)
            if v is not None:
                new_vals["webhook_enabled"] = v
            v = _prompt_edit_string("default_trust", row.default_trust)
            if v is not None:
                new_vals["default_trust"] = v
            v = _prompt_edit_list("owner_users", row.owner_users)
            if v is not None:
                new_vals["owner_users"] = v
            v = _prompt_edit_list("trusted_users", row.trusted_users)
            if v is not None:
                new_vals["trusted_users"] = v
            v = _prompt_edit_list("trusted_roles", row.trusted_roles)
            if v is not None:
                new_vals["trusted_roles"] = v
            v = _prompt_edit_bool("auto_thread", row.auto_thread)
            if v is not None:
                new_vals["auto_thread"] = v
            val = typer.prompt(
                f"  thread_hot_hours (current: {row.thread_hot_hours}, blank=keep)",
                default="",
            )
            v = val.strip()
            if v:
                try:
                    new_vals["thread_hot_hours"] = int(v)
                except ValueError:
                    typer.echo(f"    Invalid int for thread_hot_hours: {v!r} - skipped")
            if not new_vals:
                typer.echo("No changes.")
                return
            updated = dataclasses.replace(row, **new_vals)
            await store.upsert(updated)
            typer.echo(f"Updated: {', '.join(new_vals.keys())}")
        finally:
            await store.close()

    asyncio.run(_run())


def _patch(platform: str, bot_id: str, **kwargs: Any) -> None:
    """Apply a partial patch to a bot row."""
    _validate_bot_id(bot_id)
    dt = kwargs.get("default_trust")
    if dt is not None and dt not in _VALID_TRUST:
        typer.echo(
            f"Error: invalid default_trust {dt!r}. "
            f"Must be one of: {', '.join(sorted(_VALID_TRUST))}",
            err=True,
        )
        raise typer.Exit(2)

    async def _run() -> None:
        store = await _connect_bot_store()
        try:
            row = store.get(platform, bot_id)
            if row is None:
                typer.echo(
                    f"Error: {platform} bot {bot_id!r} not found in DB", err=True
                )
                raise typer.Exit(1)
            updates = {k: v for k, v in kwargs.items() if v is not None}
            if not updates:
                typer.echo("Error: no fields provided to patch", err=True)
                raise typer.Exit(1)
            updated = dataclasses.replace(row, **updates)
            await store.upsert(updated)
            typer.echo(f"Patched {platform}/{bot_id}: {', '.join(updates.keys())}")
        finally:
            await store.close()

    asyncio.run(_run())


def _remove(platform: str, bot_id: str, yes: bool = False) -> None:
    """Delete a bot and cascade its bot-agent mapping."""
    _validate_bot_id(bot_id)

    async def _run() -> None:
        bot_store = await _connect_bot_store()
        agent_store = await _connect_agent_store()
        try:
            row = bot_store.get(platform, bot_id)
            if row is None:
                typer.echo(
                    f"Error: {platform} bot {bot_id!r} not found in DB", err=True
                )
                raise typer.Exit(1)
            if not yes:
                typer.confirm(f"Delete {platform} bot {bot_id!r}?", abort=True)
            await bot_store.delete(platform, bot_id)
            await agent_store.remove_bot_agent(platform, bot_id)
            typer.echo(f"Deleted {platform}/{bot_id}")
        finally:
            await bot_store.close()
            await agent_store.close()

    asyncio.run(_run())


def _assign(platform: str, bot_id: str, agent: str) -> None:
    """Assign an agent to a bot."""
    _patch(platform, bot_id, agent=agent)


def _unassign(platform: str, bot_id: str) -> None:
    """Unassign the agent from a bot (set to empty string)."""
    _patch(platform, bot_id, agent="")


def _check_secret(secret_name: str) -> str | None:
    """Return error message if podman secret is missing, else None."""
    try:
        result = subprocess.run(
            [
                "podman",
                "secret",
                "ls",
                "--filter",
                f"name={secret_name}",
                "--format",
                "{{.Name}}",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        return "podman command not found"
    except subprocess.TimeoutExpired:
        return "podman secret ls timed out"
    if result.returncode != 0 or secret_name not in result.stdout.splitlines():
        return f"Podman secret {secret_name!r} not found"
    return None


def _validate(platform: str, bot_id: str) -> None:
    """Dry-run validation: agent exists, owners non-empty, secret exists."""
    _validate_bot_id(bot_id)

    async def _run() -> None:
        bot_store = await _connect_bot_store()
        agent_store = await _connect_agent_store()
        try:
            row = bot_store.get(platform, bot_id)
            if row is None:
                typer.echo(
                    f"Error: {platform} bot {bot_id!r} not found in DB", err=True
                )
                raise typer.Exit(1)
            errors: list[str] = []
            if row.agent:
                if agent_store.get(row.agent) is None:
                    errors.append(f"agent {row.agent!r} not found in AgentStore")
            if not row.owner_users:
                errors.append("owner_users is empty")
            secret_name = f"lyra-bot-{platform}-{bot_id}"
            err = _check_secret(secret_name)
            if err:
                errors.append(err)
            if errors:
                for e in errors:
                    typer.echo(f"Error: {e}", err=True)
                raise typer.Exit(1)
            typer.echo(f"{platform}/{bot_id}: OK")
        finally:
            await bot_store.close()
            await agent_store.close()

    asyncio.run(_run())
