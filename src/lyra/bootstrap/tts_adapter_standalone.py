"""Standalone TTS adapter — NATS request-reply wrapper around TTSService.

Runs as a separate supervisor process (lyra_tts). Subscribes to
lyra.voice.tts.request, synthesizes speech via voicecli, responds to
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

import nats

from lyra.tts import SynthesisResult, TTSConfig, TTSService, load_tts_config

log = logging.getLogger(__name__)

SUBJECT = "lyra.voice.tts.request"
QUEUE_GROUP = "tts-workers"


async def _bootstrap_tts_adapter_standalone(
    raw_config: dict,
    *,
    _stop: asyncio.Event | None = None,
) -> None:
    """Bootstrap a standalone TTS adapter process connected to NATS.

    Args:
        raw_config: Parsed config dict (lyra config.toml content).
        _stop: Optional event for graceful shutdown (tests inject this).
    """
    nats_url = os.environ.get("NATS_URL")
    if not nats_url:
        sys.exit("NATS_URL required for standalone TTS adapter")

    nc = await nats.connect(nats_url)
    log.info("tts_adapter: connected to NATS at %s", nats_url)

    tts_cfg = load_tts_config()
    tts_service = TTSService(tts_cfg)
    log.info("tts_adapter: TTSService ready (engine=%s)", tts_cfg.engine or "default")

    async def handler(msg: nats.aio.msg.Msg) -> None:
        data: dict = {}
        response: dict
        try:
            data = json.loads(msg.data)
            request_id = data.get("request_id", "unknown")
            text = data["text"]

            # Extract flat TTS kwargs from request.
            # The adapter does NOT import AgentTTSConfig (hub-layer type).
            # language, voice, and fallback_language are passed directly to
            # TTSService.synthesize() which accepts them as explicit overrides.
            # engine, accent, personality, etc. come from the global TTS config
            # (env vars) for this first pass — a future enhancement can expose
            # them as explicit kwargs once TTSService.synthesize() supports them.
            synth_kwargs: dict = {}
            for key in ("language", "voice", "fallback_language"):
                if data.get(key) is not None:
                    synth_kwargs[key] = data[key]

            result: SynthesisResult = await tts_service.synthesize(
                text,
                agent_tts=None,
                **synth_kwargs,
            )

            response = {
                "request_id": request_id,
                "ok": True,
                "audio_b64": base64.b64encode(result.audio_bytes).decode("ascii"),
                "mime_type": result.mime_type,
                "duration_ms": result.duration_ms,
                "waveform_b64": result.waveform_b64,
            }

        except Exception as exc:
            log.exception(
                "tts_adapter: synthesis failed (request_id=%s)",
                data.get("request_id", "?"),
            )
            response = {
                "request_id": data.get("request_id", "unknown"),
                "ok": False,
                "error": str(exc),
            }

        if msg.reply:
            await nc.publish(
                msg.reply,
                json.dumps(response, ensure_ascii=False).encode("utf-8"),
            )

    sub = await nc.subscribe(SUBJECT, queue=QUEUE_GROUP, cb=handler)
    log.info("tts_adapter: subscribed to %s (queue=%s)", SUBJECT, QUEUE_GROUP)

    stop = _stop if _stop is not None else asyncio.Event()
    if _stop is None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)

    await stop.wait()
    log.info("tts_adapter: shutdown signal received")

    await sub.unsubscribe()
    await nc.close()
    log.info("tts_adapter: stopped")
