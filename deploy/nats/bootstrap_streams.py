#!/usr/bin/env python3
"""Bootstrap factory-events and factory-metrics JetStream streams.

Idempotent — safe to run multiple times. Delegates to hub SSoT in
``factory.infrastructure.events.stream_setup``.

Retention policy (ops decision #1183):
  factory-events  — 24 h hot  (MaxAge=86400 s)
  factory-metrics — 7 d warm (MaxAge=604800 s)

Usage (from repo root, after NATS is running):
    uv run python deploy/nats/bootstrap_streams.py

Env:
    NATS_URL              default nats://127.0.0.1:4222
    NATS_NKEY_SEED_PATH   default ~/.roxabi/factory/nkeys/hub.seed
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

import nats

from factory.infrastructure.events.stream_setup import ensure_observability_streams

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

NATS_URL = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")
NKEY_PATH = os.environ.get(
    "NATS_NKEY_SEED_PATH", str(Path.home() / ".roxabi/factory/nkeys/hub.seed")
)


async def main() -> int:
    seed = Path(NKEY_PATH).read_text().strip() if Path(NKEY_PATH).exists() else ""
    if not seed:
        log.error("NKey seed not found at %s", NKEY_PATH)
        return 1

    kwargs: dict = {
        "servers": NATS_URL,
        "nkeys_seed_str": seed,
        "inbox_prefix": "_inbox.hub",
    }
    nc = None
    try:
        nc = await nats.connect(**kwargs)
        await ensure_observability_streams(nc.jetstream())
        log.info("All streams provisioned.")
        return 0
    except Exception:
        log.exception("Stream bootstrap failed")
        return 1
    finally:
        if nc:
            await nc.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))