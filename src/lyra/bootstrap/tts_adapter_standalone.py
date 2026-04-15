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
import sys
from dataclasses import dataclass

from lyra.nats.queue_groups import TTS_WORKERS
from lyra.tts import SynthesisResult, TTSService, load_tts_config
from roxabi_nats import NatsAdapterBase
from roxabi_nats._tts_constants import _AGENT_TTS_FIELDS
from roxabi_nats.adapter_base import CONTRACT_VERSION

log = logging.getLogger(__name__)

SUBJECT = "lyra.voice.tts.request"


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


class TtsAdapterStandalone(NatsAdapterBase):
    def __init__(self, raw_config: dict) -> None:
        super().__init__(
            SUBJECT,
            TTS_WORKERS,
            "TtsRequest",
            1,
            heartbeat_subject="lyra.voice.tts.heartbeat",
            heartbeat_interval=5.0,
        )
        self._active_count: int = 0
        tts_cfg = load_tts_config()
        self._tts_cfg = tts_cfg
        self._tts_service = TTSService(tts_cfg)
        log.info(
            "tts_adapter: TTSService ready (engine=%s)", tts_cfg.engine or "default"
        )

    def _extra_subjects(self) -> list[str]:
        return [f"{self.subject}.{self._worker_id}"]

    def _get_vram_info(self) -> tuple[int, int]:
        """Return (used_mb, total_mb). Both 0 if pynvml is unavailable."""
        try:
            import pynvml  # noqa: PLC0415  # type: ignore[import-untyped]

            pynvml.nvmlInit()
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            info = pynvml.nvmlDeviceGetMemoryInfo(handle)
            return int(info.used // (1024 * 1024)), int(info.total // (1024 * 1024))
        except Exception:
            return 0, 0

    def heartbeat_payload(self) -> dict:
        base = super().heartbeat_payload()
        vram_used, vram_total = self._get_vram_info()
        base.update(
            {
                "model_loaded": self._tts_cfg.engine or "default",
                "vram_used_mb": vram_used,
                "vram_total_mb": vram_total,
                "active_requests": self._active_count,
            }
        )
        return base

    async def handle(self, msg, payload: dict) -> None:
        data: dict = payload
        response: dict
        self._active_count += 1
        try:
            try:
                request_id = data.get("request_id", "unknown")
                text = data["text"]

                # Build a lightweight TTS config from the flat NATS request fields.
                # This mirrors AgentTTSConfig without importing the hub-layer type.
                tts_config_kwargs = {
                    k: data[k] for k in _AGENT_TTS_FIELDS if data.get(k) is not None
                }
                agent_tts = (
                    _NatsTtsConfig(**tts_config_kwargs) if tts_config_kwargs else None
                )

                synth_kwargs: dict = {}
                for key in ("language", "voice", "fallback_language"):
                    if data.get(key) is not None:
                        synth_kwargs[key] = data[key]

                result: SynthesisResult = await self._tts_service.synthesize(
                    text,
                    agent_tts=agent_tts,  # type: ignore[arg-type]  # duck-typed stand-in
                    **synth_kwargs,
                )

                response = {
                    "contract_version": CONTRACT_VERSION,
                    "request_id": request_id,
                    "ok": True,
                    "audio_b64": base64.b64encode(result.audio_bytes).decode("ascii"),
                    "mime_type": result.mime_type,
                    "duration_ms": result.duration_ms,
                    "waveform_b64": result.waveform_b64,
                }

            except Exception:
                log.exception(
                    "tts_adapter: synthesis failed (request_id=%s)",
                    data.get("request_id", "?"),
                )
                response = {
                    "contract_version": CONTRACT_VERSION,
                    "request_id": data.get("request_id", "unknown"),
                    "ok": False,
                    "error": "synthesis_failed",
                }

            await self.reply(
                msg, json.dumps(response, ensure_ascii=False).encode("utf-8")
            )
        finally:
            self._active_count -= 1


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
    log.info("tts_adapter: connecting to NATS at %s", nats_url)
    await TtsAdapterStandalone(raw_config).run(nats_url, _stop)
