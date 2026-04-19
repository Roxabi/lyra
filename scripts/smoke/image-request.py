#!/usr/bin/env python3
"""Smoke test for lyra → imagecli image-domain round-trip.

Publishes one lyra.image.generate.request and asserts ok:true on the reply.
Run on Machine 1 (hub host) after imagecli_gen is RUNNING under the new
image-worker nkey. Capture stdout as rollout evidence:

    python scripts/smoke/image-request.py \
        > artifacts/rollout-evidence/754-image-smoke.txt

Env:
    NATS_URL               Defaults to nats://127.0.0.1:4222
    NATS_NKEY_SEED_PATH    Optional; enables nkey auth when set
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

from nats.aio.client import Client as NATS

from lyra.nats.nats_image_client import (
    ImageGenParams,
    ImageUnavailableError,
    NatsImageClient,
)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", default="test image")
    parser.add_argument("--engine", default="flux2-klein")
    parser.add_argument("--width", type=int, default=512)
    parser.add_argument("--height", type=int, default=512)
    parser.add_argument("--steps", type=int, default=10)
    args = parser.parse_args()

    nats_url = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")
    seed = os.environ.get("NATS_NKEY_SEED_PATH")

    nc = NATS()
    kwargs: dict = {"servers": [nats_url]}
    if seed:
        kwargs["nkeys_seed"] = seed
    await nc.connect(**kwargs)

    client = NatsImageClient(nc, timeout=180.0)
    await client.start()

    # Give at least one heartbeat window to populate the registry:
    await asyncio.sleep(2.0)

    params = ImageGenParams(
        width=args.width,
        height=args.height,
        steps=args.steps,
    )

    try:
        resp = await client.generate(
            args.prompt,
            engine=args.engine,
            params=params,
        )
    except ImageUnavailableError as exc:
        print(f"SMOKE FAIL: {exc}", file=sys.stderr)
        await nc.drain()
        return 1

    mode = "b64" if resp.image_b64 else "file_path"
    print(
        f"SMOKE OK: request_id={resp.request_id} engine={resp.engine} "
        f"{mode} size={resp.width}x{resp.height}"
    )
    await nc.drain()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
