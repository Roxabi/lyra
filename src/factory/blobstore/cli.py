"""CLI subapp for the blobstore service — wired into root CLI in T3."""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path

import typer
import uvicorn

from factory.blobstore.serve import build_app
from factory.paths import factory_data_dir
from roxabi_blobs import FsBlobStore

blobstore_app = typer.Typer(name="blobstore", help="BlobStore HTTP service.")

_DEFAULT_TOKEN_PATH = Path("/run/secrets/factory_blobstore_token")

# Minimum sweep window — FACTORY_OUTBOUND_AUDIO JetStream MaxAge is 24 h;
# in-flight consumers may still hold references.  7 d gives margin.
_MIN_SWEEP_DAYS = 7
_DURATION_RE = re.compile(r"^(\d+)(d|h)$")


def _parse_duration_secs(value: str) -> float:
    """Parse a duration string like ``30d`` or ``48h`` into seconds.

    Raises :class:`typer.BadParameter` on invalid input.
    """
    m = _DURATION_RE.match(value.strip())
    if not m:
        raise typer.BadParameter(
            f"Invalid duration '{value}'. Use Nd (days) or Nh (hours), e.g. '30d'."
        )
    amount = int(m.group(1))
    unit = m.group(2)
    return float(amount * 86400 if unit == "d" else amount * 3600)


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
    blob_root = factory_data_dir() / "blobstore"
    blob_root.mkdir(parents=True, exist_ok=True)
    uvicorn.run(
        build_app(token_path=token_path, blob_root=blob_root),
        host=host,
        port=port,
    )


@blobstore_app.command()
def sweep(
    older_than: str = typer.Option(
        "30d",
        "--older-than",
        help="Delete refs older than this duration (e.g. '30d', '168h'). Minimum 7d.",
    ),
) -> None:
    """Delete blob refs older than the given duration.

    Files are unlinked only when their last ref is removed (ref-counted).
    Minimum sweep window is 7 days to protect in-flight consumers.
    """
    duration_secs = _parse_duration_secs(older_than)
    min_secs = _MIN_SWEEP_DAYS * 86400
    if duration_secs < min_secs:
        typer.echo(
            f"ERROR: --older-than must be at least {_MIN_SWEEP_DAYS}d "
            f"({min_secs:.0f}s). Got {duration_secs:.0f}s ({older_than}).",
            err=True,
        )
        raise typer.Exit(code=1)

    cutoff_ts = time.time() - duration_secs
    blob_root = factory_data_dir() / "blobstore"

    async def _run() -> tuple[int, int]:
        async with FsBlobStore(blob_root) as store:
            return await store.sweep_older_than(
                cutoff_ts, exclude_sources=frozenset({"soul"})
            )

    refs_deleted, files_unlinked = asyncio.run(_run())
    typer.echo(
        f"Sweep complete: {refs_deleted} ref(s) deleted, "
        f"{files_unlinked} file(s) unlinked (older-than={older_than})."
    )
