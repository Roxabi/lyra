"""NatsSttClient — hub-side NATS request-reply client for STT.

Maintains a ``VoiceWorkerRegistry`` populated from heartbeats, and routes each
transcription to the least-loaded worker via its per-worker subject
``lyra.voice.stt.request.{worker_id}``. Falls back once to the queue-group
subject (``lyra.voice.stt.request``) if the targeted worker times out.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from pathlib import Path
from uuid import uuid4

from nats.aio.client import Client as NATS

from lyra.nats.voice_health import VoiceWorkerRegistry
from lyra.stt import (
    STTNoiseError,
    STTUnavailableError,
    TranscriptionResult,
    is_whisper_noise,
)
from roxabi_nats.adapter_base import CONTRACT_VERSION
from roxabi_nats.circuit_breaker import NatsCircuitBreaker

log = logging.getLogger(__name__)

_STT_TIMEOUT_DEFAULT = 15.0
_STT_TIMEOUT_MIN = 1.0
_STT_TIMEOUT_MAX = 300.0

_HB_SUBJECT = "lyra.voice.stt.heartbeat"


def _parse_stt_timeout(timeout: float | None) -> float:
    """Resolve STT timeout: explicit arg > LYRA_STT_TIMEOUT env var > 15s default.

    Accepts values in [1.0, 300.0] seconds; falls back to 15s on invalid input.
    """
    if timeout is not None:
        value = timeout
    else:
        raw = os.environ.get("LYRA_STT_TIMEOUT", str(_STT_TIMEOUT_DEFAULT))
        try:
            value = float(raw)
        except ValueError:
            log.warning(
                "LYRA_STT_TIMEOUT=%r is not a valid float; using %.0fs",
                raw,
                _STT_TIMEOUT_DEFAULT,
            )
            return _STT_TIMEOUT_DEFAULT
    if not (_STT_TIMEOUT_MIN <= value <= _STT_TIMEOUT_MAX):
        log.warning(
            "STT timeout %.1fs out of range [%.0f, %.0f]; using %.0fs",
            value,
            _STT_TIMEOUT_MIN,
            _STT_TIMEOUT_MAX,
            _STT_TIMEOUT_DEFAULT,
        )
        return _STT_TIMEOUT_DEFAULT
    return value


class NatsSttClient:
    SUBJECT = "lyra.voice.stt.request"

    def __init__(  # noqa: PLR0913
        self,
        nc: NATS,
        *,
        timeout: float | None = None,
        model: str = "large-v3-turbo",
        language_detection_threshold: float | None = None,
        language_detection_segments: int | None = None,
        language_fallback: str | None = None,
    ) -> None:
        self._nc = nc
        self._timeout = _parse_stt_timeout(timeout)
        self._model = model
        self._detection_threshold = language_detection_threshold
        self._detection_segments = language_detection_segments
        self._detection_fallback = language_fallback
        self._cb = NatsCircuitBreaker()
        self._registry = VoiceWorkerRegistry()
        self._hb_sub = None  # set by start

    async def start(self) -> None:
        """Subscribe to heartbeat subject. Called once after nc is connected."""
        if self._hb_sub is None:
            self._hb_sub = await self._nc.subscribe(_HB_SUBJECT, cb=self._on_heartbeat)

    async def _on_heartbeat(self, msg) -> None:
        try:
            data = json.loads(msg.data)
        except Exception:
            log.debug("stt_client: heartbeat parse error", exc_info=True)
            return
        if not data.get("worker_id"):
            log.warning("stt_client: heartbeat missing worker_id, ignoring")
            return
        self._registry.record_heartbeat(data)

    async def transcribe(self, path: Path | str) -> TranscriptionResult:
        preferred = self._registry.pick_least_loaded()
        if preferred is None:
            raise STTUnavailableError("STT: no live worker (heartbeat stale >15s)")
        if self._cb.is_open():
            raise STTUnavailableError(
                "STT circuit open — adapter temporarily unavailable"
            )
        resolved = Path(path).resolve()
        audio_bytes = await asyncio.to_thread(resolved.read_bytes)
        mime = _mime_from_suffix(resolved.suffix)
        request = {
            "contract_version": CONTRACT_VERSION,
            "request_id": str(uuid4()),
            "audio_b64": base64.b64encode(audio_bytes).decode("ascii"),
            "mime_type": mime,
            "model": self._model,
            "language_detection_threshold": self._detection_threshold,
            "language_detection_segments": self._detection_segments,
            "language_fallback": self._detection_fallback,
        }
        payload = json.dumps(request, ensure_ascii=False).encode("utf-8")
        data = await self._request_with_fallback(payload, preferred.worker_id)
        if not data.get("ok"):
            self._cb.record_failure()
            raise STTUnavailableError("STT transcription failed")
        result = TranscriptionResult(
            text=data["text"],
            language=data.get("language", "unknown"),
            duration_seconds=data.get("duration_seconds", 0.0),
        )
        self._cb.record_success()
        if is_whisper_noise(result.text):
            log.info(
                "STT noise result via NATS: text=%r lang=%s",
                result.text,
                result.language,
            )
            raise STTNoiseError(f"Noise transcript: {result.text!r}")
        return result

    async def _request_with_fallback(self, payload: bytes, worker_id: str) -> dict:
        """Send to per-worker subject; on timeout, fall back once to queue group.

        Raises ``STTUnavailableError`` on final failure (after fallback), wrapping
        the originating exception. Circuit-breaker failures are recorded here.
        """
        payload_kb = len(payload) / 1024
        target = f"{self.SUBJECT}.{worker_id}"
        try:
            reply = await self._nc.request(target, payload, timeout=self._timeout)
            return json.loads(reply.data)
        except TimeoutError:
            log.warning(
                "STT: preferred worker %s timed out after %.0fs;"
                " falling back to queue group",
                worker_id,
                self._timeout,
            )
        except Exception as exc:
            return self._map_nats_exception(exc, payload_kb)
        # Fallback: queue group (round-robin among alive workers).
        try:
            reply = await self._nc.request(self.SUBJECT, payload, timeout=self._timeout)
            return json.loads(reply.data)
        except TimeoutError as exc:
            log.warning("STT adapter timeout after %.0fs", self._timeout)
            self._cb.record_failure()
            raise STTUnavailableError("STT adapter timeout") from exc
        except Exception as exc:
            return self._map_nats_exception(exc, payload_kb)

    def _map_nats_exception(self, exc: Exception, payload_kb: float) -> dict:
        """Convert a NATS request exception to STTUnavailableError; never returns."""
        if "max_payload" in str(exc).lower() or "MaxPayload" in type(exc).__name__:
            log.error(
                "STT payload too large (%.0f KB) — check NATS max_payload",
                payload_kb,
            )
            self._cb.record_failure()
            raise STTUnavailableError("STT request payload too large") from exc
        log.warning("STT adapter unreachable: %s: %s", type(exc).__name__, exc)
        self._cb.record_failure()
        raise STTUnavailableError("STT adapter unreachable") from exc

    # Backwards-compat shim for callers/tests still reading ``_worker_freshness``.
    @property
    def _worker_freshness(self) -> dict[str, float]:
        return {
            w.worker_id: w.last_heartbeat
            for w in self._registry._workers.values()
        }

    def _any_worker_alive(self) -> bool:
        return self._registry.any_alive()


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
