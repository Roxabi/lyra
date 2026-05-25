"""CLI subapp for the blobstore service — wired into root CLI in T3."""

from __future__ import annotations

from pathlib import Path

import typer
import uvicorn

from lyra.blobstore.serve import build_app

blobstore_app = typer.Typer(name="blobstore", help="BlobStore HTTP service.")

_DEFAULT_TOKEN_PATH = Path("/run/secrets/lyra_blobstore_token")


@blobstore_app.command()
def serve(
    token_path: Path = typer.Option(
        _DEFAULT_TOKEN_PATH,
        help="Path to file containing the bearer token.",
    ),
    host: str = typer.Option("0.0.0.0", help="Bind host."),
    port: int = typer.Option(8449, help="Bind port."),
) -> None:
    """Start the blobstore HTTP service."""
    blob_root = Path.home() / ".lyra" / "blobstore"
    blob_root.mkdir(parents=True, exist_ok=True)
    uvicorn.run(
        build_app(token_path=token_path, blob_root=blob_root),
        host=host,
        port=port,
    )
