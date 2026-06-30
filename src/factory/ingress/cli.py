"""CLI for factory-ingress."""

from __future__ import annotations

import asyncio

import typer
import uvicorn

from factory.infrastructure.stores.ingress.installation_store import (
    InstallationStore,
    default_db_path,
)
from factory.ingress.connectors.base import validate_factory_tenant
from factory.ingress.ports import VerificationError
from factory.ingress.serve import create_app

ingress_app = typer.Typer(
    name="ingress",
    help="Webhook ingress → factory.event.* (#2008, ADR-096).",
)

installation_app = typer.Typer(
    name="installation",
    help="Connector installation registry.",
)
ingress_app.add_typer(installation_app)


@ingress_app.command("serve")
def serve(
    host: str = typer.Option("0.0.0.0", help="Bind host"),
    port: int = typer.Option(8780, help="Bind port"),
) -> None:
    """Start the ingress HTTP server."""
    uvicorn.run(create_app(), host=host, port=port, log_level="info")


@installation_app.command("seed")
def installation_seed(
    connector: str = typer.Argument(..., help="Connector name (github, cloudflare, …)"),
    external_id: str = typer.Argument(..., help="Provider installation / account id"),
    tenant: str = typer.Option("default", "--tenant", help="Factory tenant slug"),
) -> None:
    """Seed or update a connector installation row in ingress.db."""
    try:
        validate_factory_tenant(tenant)
    except VerificationError as exc:
        raise typer.BadParameter("invalid tenant slug") from exc

    async def _run() -> None:
        store = InstallationStore(default_db_path())
        await store.connect()
        await store.seed(connector, external_id, tenant)
        await store.close()
        typer.echo(f"seeded {connector}/{external_id} → {tenant}")

    asyncio.run(_run())


@installation_app.command("list")
def installation_list() -> None:
    """List connector installation rows."""

    async def _run() -> None:
        store = InstallationStore(default_db_path())
        await store.connect()
        rows = await store.list_rows()
        await store.close()
        if not rows:
            typer.echo("(empty)")
            return
        for row in rows:
            typer.echo(
                f"{row['connector']}\t{row['external_id']}\t{row['factory_tenant']}\t"
                f"{'on' if row['enabled'] else 'off'}"
            )

    asyncio.run(_run())