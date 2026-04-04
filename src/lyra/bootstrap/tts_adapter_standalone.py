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
from dataclasses import dataclass

import nats

from lyra.tts import SynthesisResult, TTSService, load_tts_config

log = logging.getLogger(__name__)

SUBJECT = "lyra.voice.tts.request"
QUEUE_GROUP = "tts-workers"

# Fields the hub serializes from AgentTTSConfig into the NATS request.
_AGENT_TTS_FIELDS = (
    "engine", "voice", "language", "accent", "personality", "speed",
    "emotion", "exaggeration", "cfg_weight", "segment_gap", "crossfade",
    "chunk_size", "default_language", "languages",
)


@dataclass
class _NatsTtsConfig:
    """Lightweight stand-in for AgentTTSConfig — populated from NATS request fields.

    TTSService._build_generate_kwargs uses getattr(agent_tts, field, None) so any
    object with matching attributes works. This avoids importing the hub-layer type.
    """
    engine: str | None = None
    voice: str | None = None
    language: str | None = None
    accent: str | None = None
    personality: str | None = None
    speed: float | None = None
    emotion: str | None = None
    exaggeration: float | None = None
    cfg_weight: float | None = None
    segment_gap: float | None = None
    crossfade: float | None = None
    chunk_size: int | None = None
    default_language: str | None = None
    languages: list[str] | None = None


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

            # Build a lightweight TTS config from the flat NATS request fields.
            # This mirrors AgentTTSConfig without importing the hub-layer type.
            tts_config_kwargs = {
                k: data[k] for k in _AGENT_TTS_FIELDS if data.get(k) is not None
            }
            agent_tts = _NatsTtsConfig(**tts_config_kwargs) if tts_config_kwargs else None

            synth_kwargs: dict = {}
            for key in ("language", "voice", "fallback_language"):
                if data.get(key) is not None:
                    synth_kwargs[key] = data[key]

            result: SynthesisResult = await tts_service.synthesize(
                text,
                agent_tts=agent_tts,
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
                "error": "synthesis_failed",
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
