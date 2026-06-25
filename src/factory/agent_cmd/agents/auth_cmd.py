"""factory agent grant/revoke + agent auth list — ADR-090 §7 operator CLI.

Manage the agent-scoped authorization matrix (agent × principal × capability)
without a hub restart: writes land in ``auth.db``'s ``agent_grants`` table via
``AgentGrantStore``, which the hub's ``AuthorizeAgentMiddleware`` reads from a
warm cache. The MVP grants ``use`` only; ``admin`` is reserved (ADR-090 §1) and
is accepted by ``--capability`` but not yet enforced by the authorizer.
"""

from __future__ import annotations

import asyncio
from typing import Optional

import typer

from factory.cli._store_connect import _connect_grant_store
from factory.cli.agent import agent_app
from factory.core.auth.agent_grants import Capability, Principal, PrincipalKind

# Audit-trail fields the store requires non-empty (ADR-090 §1). The CLI is the
# operator surface, so "where" is fixed to ``cli``; "who" is a stable label
# until per-operator identity resolution lands.
_SOURCE = "cli"
_GRANTED_BY = "operator"

auth_app = typer.Typer(
    name="auth", help="Inspect the agent authorization matrix (ADR-090)."
)
agent_app.add_typer(auth_app)

_USER_OPT: Optional[str] = typer.Option(
    None,
    "--user",
    help="Subject: a platform-prefixed user id, e.g. tg:user:7377831990.",
)
_ROLE_OPT: Optional[str] = typer.Option(
    None, "--role", help="Subject: a platform-prefixed role id, e.g. dc:role:1234."
)
_CAP_OPT: str = typer.Option(
    "use",
    "--capability",
    help="Capability (MVP enforces 'use'; 'admin' reserved, ADR-090 §1).",
)


def _resolve_principal(user: str | None, role: str | None) -> Principal:
    """Map mutually-exclusive ``--user``/``--role`` to a Principal, or exit(1)."""
    if (user is None) == (role is None):
        typer.echo("Error: pass exactly one of --user or --role.", err=True)
        raise typer.Exit(1)
    kind = PrincipalKind.USER if user is not None else PrincipalKind.ROLE
    ident = user if user is not None else role
    assert ident is not None  # guaranteed by the exactly-one check above
    try:
        return Principal(kind=kind, id=ident)
    except ValueError as exc:
        typer.echo("Error: principal id must be non-empty.", err=True)
        raise typer.Exit(1) from exc


def _resolve_capability(raw: str) -> Capability:
    """Parse the ``--capability`` value to the enum, or exit(1)."""
    try:
        return Capability(raw)
    except ValueError as exc:
        valid = ", ".join(c.value for c in Capability)
        typer.echo(f"Error: invalid capability (expected one of: {valid}).", err=True)
        raise typer.Exit(1) from exc


@agent_app.command(name="grant")
def grant(
    agent_name: str = typer.Argument(..., help="Agent (tenant) to grant access to."),
    user: Optional[str] = _USER_OPT,
    role: Optional[str] = _ROLE_OPT,
    capability: str = _CAP_OPT,
) -> None:
    """Grant a user or role access to an agent (ADR-090 §7, no restart)."""
    principal = _resolve_principal(user, role)
    cap = _resolve_capability(capability)

    async def _run() -> None:
        store = await _connect_grant_store()
        try:
            await store.grant(
                agent_name,
                principal,
                capability=cap,
                granted_by=_GRANTED_BY,
                source=_SOURCE,
            )
        finally:
            await store.close()

    asyncio.run(_run())
    subject = f"{principal.kind.value} {principal.id}"
    typer.echo(f"Granted {cap.value} on {agent_name!r} to {subject}")


@agent_app.command(name="revoke")
def revoke(
    agent_name: str = typer.Argument(..., help="Agent (tenant) to revoke access from."),
    user: Optional[str] = _USER_OPT,
    role: Optional[str] = _ROLE_OPT,
    capability: str = _CAP_OPT,
) -> None:
    """Revoke a user's or role's access to an agent (ADR-090 §7, no restart)."""
    principal = _resolve_principal(user, role)
    cap = _resolve_capability(capability)
    removed = False

    async def _run() -> None:
        nonlocal removed
        store = await _connect_grant_store()
        try:
            removed = await store.revoke(agent_name, principal, capability=cap)
        finally:
            await store.close()

    asyncio.run(_run())
    subject = f"{principal.kind.value} {principal.id}"
    if removed:
        typer.echo(f"Revoked {cap.value} on {agent_name!r} from {subject}")
    else:
        typer.echo(f"No {cap.value} grant on {agent_name!r} for {subject}")


@auth_app.command(name="list")
def list_grants(
    agent_name: str = typer.Argument(..., help="Agent whose grant matrix to list."),
) -> None:
    """List every grant recorded for an agent (ADR-090 §7)."""

    async def _run() -> None:
        store = await _connect_grant_store()
        try:
            grants = store.list_grants(agent_name)
            if not grants:
                typer.echo(f"  (no grants for agent {agent_name!r})")
                return
            typer.echo(
                f"{'KIND':<5} {'PRINCIPAL':<26} {'CAP':<5} {'BY':<10} CREATED_AT"
            )
            for g in grants:
                p = g.principal
                typer.echo(
                    f"{p.kind.value:<5} {p.id:<26} {g.capability.value:<5} "
                    f"{g.granted_by:<10} {g.created_at.isoformat()}"
                )
        finally:
            await store.close()

    asyncio.run(_run())
