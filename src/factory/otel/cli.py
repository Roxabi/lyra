"""CLI subapp for factory-otel — wired into root CLI."""

from __future__ import annotations

from pathlib import Path

import typer
import uvicorn

from factory.otel.grpc_server import start_grpc_server
from factory.otel.serve import build_app
from factory.otel.store import OtelRawStore

otel_app = typer.Typer(
    name="otel",
    help="OTel raw store — OTLP ingest and span query API.",
)

_DEFAULT_TOKEN_PATH = Path("/run/secrets/factory_otel_token")


@otel_app.command()
def serve(
    token_path: Path = typer.Option(
        _DEFAULT_TOKEN_PATH,
        help="Path to file containing the bearer token.",
    ),
    host: str = typer.Option("0.0.0.0", help="HTTP bind host."),
    http_port: int = typer.Option(8450, help="HTTP API + OTLP HTTP port."),
    grpc_port: int = typer.Option(4317, help="OTLP gRPC port."),
) -> None:
    """Start factory-otel (OTLP ingest + JSONL/SQLite + GET /api/spans)."""
    store = OtelRawStore()
    store.ensure_dirs()
    token = token_path.read_text().strip()
    start_grpc_server(store, port=grpc_port, token=token)
    uvicorn.run(
        build_app(token=token, store=store),
        host=host,
        port=http_port,
    )