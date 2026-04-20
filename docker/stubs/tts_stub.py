#!/usr/bin/env python3
"""TTS NATS stub — returns silent WAV bytes, publishes heartbeats.

Subscribes to:
  lyra.voice.tts.request           (queue group: tts-workers)
  lyra.voice.tts.request.<worker_id>  (direct routing for load-aware #603)

Publishes:
  lyra.voice.tts.heartbeat         (every 5s)

Response envelope matches `roxabi_contracts.voice.TtsResponse` (ok,
request_id, contract_version, trace_id, issued_at, audio_b64, mime_type,
duration_ms) — per ADR-049.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import signal
import time
from datetime import datetime, timezone

import nats

NATS_URL = os.getenv("NATS_URL", "nats://localhost:4222")
WORKER_ID = os.getenv("TTS_STUB_WORKER_ID", "tts-stub-01")
VRAM_USED = int(os.getenv("TTS_STUB_VRAM_USED_MB", "4800"))
VRAM_TOTAL = int(os.getenv("TTS_STUB_VRAM_TOTAL_MB", "16384"))
HB_INTERVAL = float(os.getenv("TTS_STUB_HB_INTERVAL", "5"))
CONTRACT_VERSION = "1"

# Minimal valid WAV: 44-byte header + 1 sample of silence
_SILENT_WAV = (
    b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00"
    b"\x01\x00\x01\x00\x80\xbb\x00\x00\x00w\x01\x00"
    b"\x02\x00\x10\x00data\x02\x00\x00\x00\x00\x00"
)
_SILENT_WAV_B64 = base64.b64encode(_SILENT_WAV).decode()

_active = 0
_start = time.monotonic()
_stop = asyncio.Event()


async def handle_request(msg: nats.aio.client.Msg) -> None:
    global _active
    _active += 1
    try:
        try:
            request = json.loads(msg.data)
        except Exception:
            request = {}
        response = {
            "ok": True,
            "contract_version": request.get("contract_version", CONTRACT_VERSION),
            "trace_id": request.get("trace_id", "stub-trace"),
            "issued_at": datetime.now(timezone.utc).isoformat(),
            "request_id": request.get("request_id", "stub-req"),
            "audio_b64": _SILENT_WAV_B64,
            "mime_type": "audio/wav",
            "duration_ms": 10,
            "waveform_b64": None,
            "error": None,
        }
        await msg.respond(json.dumps(response).encode())
    finally:
        _active -= 1


async def heartbeat_loop(nc: nats.aio.client.Client) -> None:
    while not _stop.is_set():
        payload = {
            "worker_id": WORKER_ID,
            "service": "tts",
            "host": "docker-stub",
            "vram_used_mb": VRAM_USED,
            "vram_total_mb": VRAM_TOTAL,
            "model_loaded": "stub",
            "active_requests": _active,
            "uptime_s": int(time.monotonic() - _start),
            "ts": int(time.time()),
        }
        await nc.publish("lyra.voice.tts.heartbeat", json.dumps(payload).encode())
        await asyncio.sleep(HB_INTERVAL)


async def main() -> None:
    print(f"[tts-stub] connecting to {NATS_URL} as {WORKER_ID}", flush=True)
    nc = await nats.connect(NATS_URL)

    await nc.subscribe("lyra.voice.tts.request", queue="tts-workers", cb=handle_request)
    await nc.subscribe(f"lyra.voice.tts.request.{WORKER_ID}", cb=handle_request)
    print(f"[tts-stub] ready — queue=tts-workers worker={WORKER_ID}", flush=True)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _stop.set)

    await asyncio.gather(heartbeat_loop(nc), _stop.wait())
    await nc.drain()
    print("[tts-stub] stopped", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
