"""NatsSttClient — hub-side NATS request-reply client for STT."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from pathlib import Path
from uuid import uuid4

from nats.aio.client import Client as NATS

from lyra.stt import STTUnavailableError, TranscriptionResult

log = logging.getLogger(__name__)


class NatsSttClient:
    SUBJECT = "lyra.voice.stt.request"

    def __init__(
        self,
        nc: NATS,
        *,
        timeout: float = 60.0,
        model: str = "large-v3-turbo",
        language_detection_threshold: float | None = None,
        language_detection_segments: int | None = None,
        language_fallback: str | None = None,
    ) -> None:
        self._nc = nc
        self._timeout = timeout
        self._model = model
        self._detection_threshold = language_detection_threshold
        self._detection_segments = language_detection_segments
        self._detection_fallback = language_fallback

    async def transcribe(self, path: Path | str) -> TranscriptionResult:
        resolved = Path(path).resolve()
        audio_bytes = await asyncio.to_thread(resolved.read_bytes)
        mime = _mime_from_suffix(resolved.suffix)
        request = {
            "request_id": str(uuid4()),
            "audio_b64": base64.b64encode(audio_bytes).decode("ascii"),
            "mime_type": mime,
            "model": self._model,
            "language_detection_threshold": self._detection_threshold,
            "language_detection_segments": self._detection_segments,
            "language_fallback": self._detection_fallback,
        }
        payload = json.dumps(request, ensure_ascii=False).encode("utf-8")
        payload_kb = len(payload) / 1024
        try:
            reply = await self._nc.request(self.SUBJECT, payload, timeout=self._timeout)
            data = json.loads(reply.data)
        except TimeoutError as exc:
            log.warning("STT adapter timeout after %.0fs", self._timeout)
            raise STTUnavailableError("STT adapter timeout") from exc
        except Exception as exc:
            if "max_payload" in str(exc).lower() or "MaxPayload" in type(exc).__name__:
                log.error(
                    "STT payload too large (%.0f KB)"
                    " — check NATS max_payload",
                    payload_kb,
                )
                raise STTUnavailableError("STT request payload too large") from exc
            log.warning("STT adapter unreachable: %s: %s", type(exc).__name__, exc)
            raise STTUnavailableError("STT adapter unreachable") from exc
        if not data.get("ok"):
            raise STTUnavailableError("STT transcription failed")
        return TranscriptionResult(
            text=data["text"],
            language=data.get("language", "unknown"),
            duration_seconds=data.get("duration_seconds", 0.0),
        )


def _mime_from_suffix(suffix: str) -> str:
    return {
        ".ogg": "audio/ogg",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".m4a": "audio/mp4",
        ".webm": "audio/webm",
        ".flac": "audio/flac",
        ".opus": "audio/ogg",
    }.get(suffix.lower(), "audio/ogg")
