"""factory user — canonical identity registry (UserStore)."""

from __future__ import annotations

import asyncio

import typer

from factory.cli._store_connect import _connect_user_store
from factory.core.auth.platform_keys import is_platform_key, is_user_id

user_app = typer.Typer(name="user", help="Canonical user identities (rx:user:…).")


def _require_platform_key(identity: str) -> str:
    if not is_platform_key(identity):
        typer.echo(
            f"Error: {identity!r} is not a platform key (tg:user:… or dc:user:…).",
            err=True,
        )
        raise typer.Exit(1)
    return identity


@user_app.command("list")
def list_users() -> None:
    """List all registered canonical users."""

    async def _run() -> None:
        store = await _connect_user_store()
        try:
            users = await store.list_users()
            if not users:
                typer.echo("  (no users registered)")
                return
            typer.echo(f"{'USER_ID':<40} {'DISPLAY':<16} CREATED_AT")
            for user in users:
                name = user.display_name or ""
                typer.echo(
                    f"{user.id:<40} {name:<16} {user.created_at.isoformat()}"
                )
        finally:
            await store.close()

    asyncio.run(_run())


@user_app.command("show")
def show_user(
    identity: str = typer.Argument(
        ...,
        help="Platform key (tg:user:…) or canonical id (rx:user:…).",
    ),
) -> None:
    """Show a user and linked platform identities."""

    async def _run() -> None:
        store = await _connect_user_store()
        try:
            if is_user_id(identity):
                user_id = identity
            elif is_platform_key(identity):
                user_id = store.resolve_user_id(identity)
                if user_id is None:
                    typer.echo(f"No canonical user for {identity!r}")
                    raise typer.Exit(1)
            else:
                typer.echo(
                    "Error: identity must be a platform key or rx:user:… id.",
                    err=True,
                )
                raise typer.Exit(1)

            user = await store.get_user(user_id)
            if user is None:
                typer.echo(f"User {user_id!r} not found")
                raise typer.Exit(1)

            typer.echo(f"user_id:      {user.id}")
            typer.echo(f"display_name: {user.display_name or '(none)'}")
            typer.echo(f"created_at:   {user.created_at.isoformat()}")
            typer.echo("platform_identities:")
            identities = await store.list_platform_identities(user_id)
            if not identities:
                typer.echo("  (none)")
            for ident in identities:
                typer.echo(
                    f"  {ident.platform_key}  ({ident.platform}, linked "
                    f"{ident.linked_at.isoformat()})"
                )
        finally:
            await store.close()

    asyncio.run(_run())


@user_app.command("register")
def register_user(
    platform_key: str = typer.Argument(
        ...,
        help="Platform identity to register (e.g. tg:user:7377831990).",
    ),
) -> None:
    """Register a platform identity and return its canonical rx:user: id."""

    async def _run() -> None:
        key = _require_platform_key(platform_key)
        store = await _connect_user_store()
        try:
            user_id = await store.ensure_user(key)
            typer.echo(f"Registered {key} → {user_id}")
        finally:
            await store.close()

    asyncio.run(_run())


@user_app.command("link")
def link_users(
    primary: str = typer.Argument(..., help="Primary platform key."),
    secondary: str = typer.Argument(..., help="Secondary platform key to merge."),
) -> None:
    """Link two platform identities under one canonical user."""

    async def _run() -> None:
        primary_key = _require_platform_key(primary)
        secondary_key = _require_platform_key(secondary)
        store = await _connect_user_store()
        try:
            user_id = await store.link_platform_keys(primary_key, secondary_key)
            typer.echo(f"Linked {primary_key} + {secondary_key} → {user_id}")
        except ValueError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(1) from exc
        finally:
            await store.close()

    asyncio.run(_run())


@user_app.command("unlink")
def unlink_user(
    platform_key: str = typer.Argument(
        ...,
        help="Platform identity to detach into its own solo user.",
    ),
) -> None:
    """Unlink a platform identity from its linked group."""

    async def _run() -> None:
        key = _require_platform_key(platform_key)
        store = await _connect_user_store()
        try:
            ok = await store.unlink_platform_key(key)
            if not ok:
                typer.echo(f"Cannot unlink {key!r} (not linked or sole identity)")
                raise typer.Exit(1)
            user_id = store.resolve_user_id(key)
            typer.echo(f"Unlinked {key} → solo user {user_id}")
        finally:
            await store.close()

    asyncio.run(_run())