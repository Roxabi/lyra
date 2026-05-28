#!/usr/bin/env python3
"""Bootstrap lyra-events and lyra-metrics JetStream streams.

Idempotent — safe to run multiple times. Uses the add→BadRequestError→update
pattern mirroring turn_writer/stream_setup.py.

Retention policy (ops decision #1183):
  lyra-events  — 24 h hot  (MaxAge=86400 s)
  lyra-metrics — 7 d warm (MaxAge=604800 s)

Usage (from repo root, after NATS is running):
    uv run python deploy/nats/bootstrap_streams.py

Env:
    NATS_URL              default nats://127.0.0.1:4222
    NATS_NKEY_SEED_PATH   default ~/.lyra/nkeys/hub.seed
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from nats.js.api import RetentionPolicy, StorageType, StreamConfig
from nats.js.errors import BadRequestError

import nats

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

NATS_URL = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")
NKEY_PATH = os.environ.get(
    "NATS_NKEY_SEED_PATH", str(Path.home() / ".lyra/nkeys/hub.seed")
)

STREAMS: dict[str, dict] = {
    "lyra-events": {
        "subjects": ["lyra.event.>"],
        "retention": RetentionPolicy.LIMITS,
        "max_age": 24 * 60 * 60,  # 24 hours (hot)
        "max_bytes": 512 * 1024 * 1024,  # 512 MiB
        "storage": StorageType.FILE,
        "duplicate_window": 120,  # 2 min dedup
    },
    "lyra-metrics": {
        "subjects": ["lyra.metric.>"],
        "retention": RetentionPolicy.LIMITS,
        "max_age": 7 * 24 * 60 * 60,  # 7 days (warm)
        "max_bytes": 256 * 1024 * 1024,  # 256 MiB
        "storage": StorageType.FILE,
        "duplicate_window": 60,  # 1 min dedup
    },
}


async def ensure_stream(js, name: str, cfg: dict) -> None:
    stream_cfg = StreamConfig(
        name=name,
        subjects=cfg["subjects"],
        retention=cfg["retention"],
        storage=cfg["storage"],
        max_age=float(cfg["max_age"]),
        max_bytes=cfg["max_bytes"],
        duplicate_window=cfg["duplicate_window"],
    )
    try:
        await js.add_stream(stream_cfg)
        log.info("Stream %s created", name)
    except BadRequestError:
        try:
            await js.update_stream(stream_cfg)
            log.info("Stream %s config updated", name)
        except nats.errors.Error:
            log.exception("Stream %s update failed", name)
            raise
    except nats.errors.Error:
        log.exception("Stream %s add failed", name)
        raise


async def main() -> int:
    seed = Path(NKEY_PATH).read_text().strip() if Path(NKEY_PATH).exists() else ""
    if not seed:
        log.error("NKey seed not found at %s", NKEY_PATH)
        return 1

    kwargs: dict = {"servers": NATS_URL, "nkeys_seed_str": seed, "inbox_prefix": "_inbox.hub"}
    nc = None
    try:
        nc = await nats.connect(**kwargs)
        js = nc.jetstream()
        for name, cfg in STREAMS.items():
            await ensure_stream(js, name, cfg)
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
