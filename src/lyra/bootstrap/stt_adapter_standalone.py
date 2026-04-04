"""Standalone STT adapter — NATS request-reply wrapper around STTService.

Runs as a separate supervisor process (lyra_stt). Subscribes to
lyra.voice.stt.request, transcribes audio via voicecli, responds to
the reply-to inbox. The hub never imports voicecli — this process does.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import signal
import sys
import tempfile
from pathlib import Path

import nats

from lyra.stt import STTService, TranscriptionResult, load_stt_config

log = logging.getLogger(__name__)

SUBJECT = "lyra.voice.stt.request"
QUEUE_GROUP = "stt-workers"


async def _bootstrap_stt_adapter_standalone(
    raw_config: dict,
    *,
    _stop: asyncio.Event | None = None,
) -> None:
    """Bootstrap a standalone STT adapter process connected to NATS.

    Args:
        raw_config: Parsed config dict (lyra config.toml content).
        _stop: Optional event for graceful shutdown (tests inject this).
    """
    nats_url = os.environ.get("NATS_URL")
    if not nats_url:
        sys.exit("NATS_URL required for standalone STT adapter")

    nc = await nats.connect(nats_url)
    log.info("stt_adapter: connected to NATS at %s", nats_url)

    base_stt_cfg = load_stt_config()
    stt_service = STTService(base_stt_cfg)
    log.info("stt_adapter: STTService ready (model=%s)", base_stt_cfg.model_size)

    async def handler(msg: nats.aio.msg.Msg) -> None:
        data: dict = {}
        response: dict
        try:
            data = json.loads(msg.data)
            request_id = data.get("request_id", "unknown")
            audio_b64 = data["audio_b64"]
            audio_bytes = base64.b64decode(audio_b64)
            mime_type = data.get("mime_type", "audio/ogg")

            # Apply per-request STT config overrides from hub (detection params)
            svc = stt_service
            overrides: dict = {}
            for key in (
                "language_detection_threshold",
                "language_detection_segments",
                "language_fallback",
            ):
                if data.get(key) is not None:
                    overrides[key] = data[key]
            if overrides:
                cfg = base_stt_cfg.model_copy(update=overrides)
                svc = STTService(cfg)

            suffix = _mime_to_ext(mime_type)
            fd, tmp_path_str = tempfile.mkstemp(suffix=suffix)
            try:
                os.write(fd, audio_bytes)
                os.close(fd)
                tmp_path = Path(tmp_path_str)

                result: TranscriptionResult = await svc.transcribe(tmp_path)

                response = {
                    "request_id": request_id,
                    "ok": True,
                    "text": result.text,
                    "language": result.language,
                    "duration_seconds": result.duration_seconds,
                }
            finally:
                Path(tmp_path_str).unlink(missing_ok=True)

        except Exception:
            log.exception(
                "stt_adapter: transcription failed (request_id=%s)",
                data.get("request_id", "?"),
            )
            response = {
                "request_id": data.get("request_id", "unknown"),
                "ok": False,
                "error": "transcription_failed",
            }

        if msg.reply:
            await nc.publish(
                msg.reply,
                json.dumps(response, ensure_ascii=False).encode("utf-8"),
            )

    sub = await nc.subscribe(SUBJECT, queue=QUEUE_GROUP, cb=handler)
    log.info("stt_adapter: subscribed to %s (queue=%s)", SUBJECT, QUEUE_GROUP)

    stop = _stop if _stop is not None else asyncio.Event()
    if _stop is None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)

    await stop.wait()
    log.info("stt_adapter: shutdown signal received")

    await sub.unsubscribe()
    await nc.close()
    log.info("stt_adapter: stopped")


def _mime_to_ext(mime_type: str) -> str:
    """Map a MIME type to a file extension for the temp audio file."""
    return {
        "audio/ogg": ".ogg",
        "audio/mpeg": ".mp3",
        "audio/mp4": ".m4a",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/webm": ".webm",
        "audio/flac": ".flac",
    }.get(mime_type, ".ogg")
