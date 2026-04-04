"""NATS connection helper with optional nkey authentication."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

import nats
from nats.aio.client import Client as NATS

log = logging.getLogger(__name__)


def _read_nkey_seed() -> str | None:
    """Read nkey seed from file path in NATS_NKEY_SEED_PATH env var.

    Returns None if env var is unset (dev mode).
    Exits with clear error if path is not a file, unreadable, or empty.
    """
    path_str = os.environ.get("NATS_NKEY_SEED_PATH")
    if not path_str:
        return None
    path = Path(path_str)
    if not path.is_file():
        sys.exit(f"NATS_NKEY_SEED_PATH={path_str!r} is not a file")
    try:
        seed = path.read_text().strip()
    except OSError as exc:
        sys.exit(f"NATS_NKEY_SEED_PATH={path_str!r} is unreadable: {exc}")
    if not seed:
        sys.exit(f"NATS_NKEY_SEED_PATH={path_str!r} is empty")
    return seed


async def nats_connect(url: str) -> NATS:
    """Connect to NATS, optionally authenticating with an nkey seed.

    If NATS_NKEY_SEED_PATH is set, reads the seed file and passes it
    via nkeys_seed_str. If unset, connects without authentication (dev mode).
    """
    kwargs: dict[str, Any] = {}
    seed = _read_nkey_seed()
    if seed:
        kwargs["nkeys_seed_str"] = seed
        log.info("NATS nkey auth enabled (seed from NATS_NKEY_SEED_PATH)")
    return await nats.connect(url, **kwargs)
