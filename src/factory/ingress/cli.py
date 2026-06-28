"""CLI for factory-ingress."""

from __future__ import annotations

import typer
import uvicorn

from factory.ingress.serve import create_app

ingress_app = typer.Typer(
    name="ingress",
    help="Webhook ingress → factory.event.* (#2008).",
)


@ingress_app.command("serve")
def serve(
    host: str = typer.Option("0.0.0.0", help="Bind host"),
    port: int = typer.Option(8780, help="Bind port"),
) -> None:
    """Start the ingress HTTP server."""
    uvicorn.run(create_app(), host=host, port=port, log_level="info")