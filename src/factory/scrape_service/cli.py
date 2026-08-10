"""CLI: factory scrape serve — HTTP scrape provider (#2327)."""

from __future__ import annotations

import typer
import uvicorn

from factory.scrape_service.app import build_app

scrape_app = typer.Typer(name="scrape", help="HTTP scrape service (#2327).")


@scrape_app.command("serve")
def serve(
    host: str = typer.Option("0.0.0.0", help="Bind host."),
    port: int = typer.Option(8455, help="Bind port."),
) -> None:
    """Start the scrape HTTP service."""
    uvicorn.run(build_app(), host=host, port=port)
