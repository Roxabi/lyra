"""NATS identity helpers for ``factory ops`` commands.

Kept separate from ``ops`` so the file-length gate stays under budget.
"""

from __future__ import annotations

import os
from pathlib import Path

import typer

_DEFAULT_NATS_URL = "nats://localhost:4222"


def inbox_prefix_for_seed(seed_path: Path) -> str:
    """Derive ADR-051 inbox prefix from seed filename (hub.seed → _inbox.hub)."""
    return f"_inbox.{seed_path.stem}"


def default_nats_url() -> str:
    return os.environ.get("NATS_URL", _DEFAULT_NATS_URL).strip() or _DEFAULT_NATS_URL


def default_hub_seed(seeds_dir: Path) -> Path:
    # Disk SSoT per deploy/secrets-policy.toml → factory-nats-hub ← nkeys/hub.seed
    return seeds_dir / "hub.seed"


def seed_path_for(seeds_dir: Path, name: str) -> Path:
    """Resolve ``{seeds_dir}/{name}.seed`` and ensure it stays inside *seeds_dir*."""
    candidate = (seeds_dir / f"{name}.seed").resolve()
    base = seeds_dir.resolve()
    if not candidate.is_relative_to(base):
        raise typer.BadParameter(f"identity name {name!r} resolves outside seeds-dir")
    return candidate