#!/usr/bin/env python3
"""STT NATS stub — returns canned transcription, publishes heartbeats.

Subscribes to:
  lyra.voice.stt.request           (queue group: stt-workers)
  lyra.voice.stt.request.<worker_id>  (direct routing for load-aware #603)

Publishes:
  lyra.voice.stt.heartbeat         (every 5s)
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import time

import nats

NATS_URL = os.getenv("NATS_URL", "nats://localhost:4222")
WORKER_ID = os.getenv("STT_STUB_WORKER_ID", "stt-stub-01")
VRAM_USED = int(os.getenv("STT_STUB_VRAM_USED_MB", "2400"))
VRAM_TOTAL = int(os.getenv("STT_STUB_VRAM_TOTAL_MB", "16384"))
TRANSCRIPT = os.getenv("STT_STUB_TRANSCRIPT", "hello from stub")
HB_INTERVAL = float(os.getenv("STT_STUB_HB_INTERVAL", "5"))

_active = 0
_start = time.monotonic()
_stop = asyncio.Event()


async def handle_request(msg: nats.aio.client.Msg) -> None:
    global _active
    _active += 1
    try:
        response = {
            "text": TRANSCRIPT,
            "language": "en",
            "duration": 1.0,
            "error": None,
        }
        await msg.respond(json.dumps(response).encode())
    finally:
        _active -= 1


async def heartbeat_loop(nc: nats.aio.client.Client) -> None:
    while not _stop.is_set():
        payload = {
            "worker_id": WORKER_ID,
            "service": "stt",
            "host": "docker-stub",
            "vram_used_mb": VRAM_USED,
            "vram_total_mb": VRAM_TOTAL,
            "model_loaded": "stub",
            "active_requests": _active,
            "uptime_s": int(time.monotonic() - _start),
            "ts": int(time.time()),
        }
        await nc.publish("lyra.voice.stt.heartbeat", json.dumps(payload).encode())
        await asyncio.sleep(HB_INTERVAL)


async def main() -> None:
    print(f"[stt-stub] connecting to {NATS_URL} as {WORKER_ID}", flush=True)
    nc = await nats.connect(NATS_URL)

    await nc.subscribe("lyra.voice.stt.request", queue="stt-workers", cb=handle_request)
    await nc.subscribe(f"lyra.voice.stt.request.{WORKER_ID}", cb=handle_request)
    print(f"[stt-stub] ready — queue=stt-workers worker={WORKER_ID}", flush=True)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _stop.set)

    await asyncio.gather(heartbeat_loop(nc), _stop.wait())
    await nc.drain()
    print("[stt-stub] stopped", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
