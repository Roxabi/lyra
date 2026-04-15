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
import sys
import tempfile
from pathlib import Path

from lyra.nats.queue_groups import STT_WORKERS
from lyra.stt import STTService, TranscriptionResult, load_stt_config
from roxabi_nats import NatsAdapterBase
from roxabi_nats.adapter_base import CONTRACT_VERSION

log = logging.getLogger(__name__)

SUBJECT = "lyra.voice.stt.request"


class SttAdapterStandalone(NatsAdapterBase):
    def __init__(self, raw_config: dict) -> None:
        super().__init__(
            SUBJECT,
            STT_WORKERS,
            "SttRequest",
            1,
            heartbeat_subject="lyra.voice.stt.heartbeat",
            heartbeat_interval=5.0,
        )
        self._active_count: int = 0
        self._base_stt_cfg = load_stt_config()
        self._stt_service = STTService(self._base_stt_cfg)
        log.info(
            "stt_adapter: STTService ready (model=%s)", self._base_stt_cfg.model_size
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
                "model_loaded": self._base_stt_cfg.model_size,
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
                audio_b64 = data["audio_b64"]
                audio_bytes = base64.b64decode(audio_b64)
                mime_type = data.get("mime_type", "audio/ogg")

                svc = self._stt_service
                overrides: dict = {}
                for key in (
                    "language_detection_threshold",
                    "language_detection_segments",
                    "language_fallback",
                ):
                    if data.get(key) is not None:
                        overrides[key] = data[key]
                if overrides:
                    cfg = self._base_stt_cfg.model_copy(update=overrides)
                    svc = STTService(cfg)

                suffix = _mime_to_ext(mime_type)
                fd, tmp_path_str = tempfile.mkstemp(suffix=suffix)
                try:
                    os.write(fd, audio_bytes)
                    os.close(fd)
                    tmp_path = Path(tmp_path_str)
                    result: TranscriptionResult = await svc.transcribe(tmp_path)
                    response = {
                        "contract_version": CONTRACT_VERSION,
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
                    "contract_version": CONTRACT_VERSION,
                    "request_id": data.get("request_id", "unknown"),
                    "ok": False,
                    "error": "transcription_failed",
                }

            await self.reply(
                msg, json.dumps(response, ensure_ascii=False).encode("utf-8")
            )
        finally:
            self._active_count -= 1


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
    log.info("stt_adapter: connecting to NATS at %s", nats_url)
    await SttAdapterStandalone(raw_config).run(nats_url, _stop)


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
