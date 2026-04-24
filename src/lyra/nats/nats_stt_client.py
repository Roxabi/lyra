"""NatsSttClient — hub-side NATS request-reply client for STT.

Maintains a ``WorkerRegistry`` populated from heartbeats, and routes each
transcription to workers in score order via per-worker subject
``lyra.voice.stt.request.{worker_id}``. Walks the registry on timeout or
NoRespondersError, marking stale workers and trying the next candidate.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from datetime import datetime, timezone
from typing import NoReturn
from uuid import uuid4

from nats.aio.client import Client as NATS
from nats.errors import NoRespondersError
from pydantic import ValidationError

from lyra.nats.worker_registry import WorkerRegistry
from lyra.stt import (
    STTNoiseError,
    STTUnavailableError,
    TranscriptionResult,
    is_whisper_noise,
)
from roxabi_contracts.envelope import CONTRACT_VERSION
from roxabi_contracts.voice import (
    SUBJECTS,
    SttRequest,
    SttResponse,
    per_worker_stt,
    validate_worker_id,
)
from roxabi_nats.circuit_breaker import NatsCircuitBreaker

log = logging.getLogger(__name__)

_STT_TIMEOUT_DEFAULT = 15.0
_STT_TIMEOUT_MIN = 1.0
_STT_TIMEOUT_MAX = 300.0


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


def _is_no_responders(exc: Exception) -> bool:
    """Check if the exception is a NATS NoRespondersError."""
    return isinstance(exc, NoRespondersError)


class NatsSttClient:
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
        self._registry = WorkerRegistry()
        self._hb_sub = None  # set by start

    async def start(self) -> None:
        """Subscribe to heartbeat subject. Called once after nc is connected."""
        if self._hb_sub is None:
            self._hb_sub = await self._nc.subscribe(
                SUBJECTS.stt_heartbeat, cb=self._on_heartbeat
            )

    async def _on_heartbeat(self, msg) -> None:
        try:
            data = json.loads(msg.data)
        except json.JSONDecodeError:
            log.debug("stt_client: heartbeat parse error", exc_info=True)
            return
        worker_id = data.get("worker_id")
        if not worker_id:
            log.warning("stt_client: heartbeat missing worker_id, ignoring")
            return
        if not isinstance(worker_id, str):
            log.warning(
                "stt_client: heartbeat non-string worker_id=%r, ignoring", worker_id
            )
            return
        # Receive-side match for the PUBLISH-path safe-chars enforcement in
        # per_worker_stt. Without this, a rogue worker publishing a heartbeat
        # with a wildcard-bearing id (e.g. "evil.worker.*") would pollute the
        # registry until first routing attempt.
        try:
            validate_worker_id(worker_id)
        except ValueError:
            log.warning(
                "stt_client: heartbeat with unsafe worker_id=%r, ignoring",
                worker_id,
            )
            return
        self._registry.record_heartbeat(data)

    def _parse_reply(self, raw: bytes) -> SttResponse:
        """Validate a NATS reply against SttResponse; translate a ValidationError
        into STTUnavailableError + record a circuit-breaker failure."""
        try:
            return SttResponse.model_validate_json(raw)
        except ValidationError as exc:
            self._cb.record_failure()
            raise STTUnavailableError("STT reply failed schema validation") from exc

    async def _walk_registry(self, payload: bytes) -> SttResponse:
        """Iterate over workers in score order until one succeeds.

        On TimeoutError/NoRespondersError: mark worker stale, continue to next.
        On other exceptions: raise via _raise_nats_failure (terminal).
        After exhaustion: record_failure once, raise STTUnavailableError chained
        from last exception.
        """
        candidates = self._registry.ordered_by_score()
        if not candidates:
            raise STTUnavailableError("STT: no live worker (heartbeat stale >15s)")

        payload_kb = len(payload) / 1024
        last_exc: Exception | None = None

        for worker in candidates:
            target = per_worker_stt(worker.worker_id)
            try:
                reply = await self._nc.request(target, payload, timeout=self._timeout)
                return self._parse_reply(reply.data)
            except TimeoutError:
                self._registry.mark_stale(worker.worker_id)
                last_exc = TimeoutError()
                continue
            except Exception as exc:
                if _is_no_responders(exc):
                    self._registry.mark_stale(worker.worker_id)
                    last_exc = exc
                    continue
                # Terminal: other exceptions go through failure path
                self._raise_nats_failure(exc, payload_kb)

        # All candidates exhausted
        log.warning(
            "STT: all workers unresponsive, last error type=%s",
            type(last_exc).__name__ if last_exc else "None",
        )
        self._cb.record_failure()
        raise STTUnavailableError("STT: all workers unresponsive") from last_exc

    async def transcribe(self, audio: bytes, mime: str) -> TranscriptionResult:
        if self._cb.is_open():
            raise STTUnavailableError(
                "STT circuit open — adapter temporarily unavailable"
            )
        request = SttRequest(
            contract_version=CONTRACT_VERSION,
            trace_id=str(uuid4()),
            issued_at=datetime.now(timezone.utc),
            request_id=str(uuid4()),
            audio_b64=base64.b64encode(audio).decode("ascii"),
            mime_type=mime,
            model=self._model,
            language_detection_threshold=self._detection_threshold,
            language_detection_segments=self._detection_segments,
            language_fallback=self._detection_fallback,
        )
        payload = request.model_dump_json(exclude_none=True).encode("utf-8")
        resp = await self._walk_registry(payload)
        if not resp.ok:
            self._cb.record_failure()
            raise STTUnavailableError(resp.error or "STT transcription failed")
        # SttResponse._enforce_success_invariant guarantees text, language, and
        # duration_seconds are non-null whenever ok=True. Asserting narrows the
        # types and fails loudly if that invariant ever drifts.
        assert resp.text is not None
        assert resp.language is not None
        assert resp.duration_seconds is not None
        result = TranscriptionResult(
            text=resp.text,
            language=resp.language,
            duration_seconds=resp.duration_seconds,
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

    def _raise_nats_failure(self, exc: Exception, payload_kb: float) -> NoReturn:
        """Convert a NATS request exception to STTUnavailableError.

        Domain errors (``STTUnavailableError``) pass through unchanged so callers
        can rely on this being the single translation boundary for NATS-transport
        exceptions — no per-site ``except STTUnavailableError: raise`` guard
        needed anywhere in this file.
        """
        if isinstance(exc, STTUnavailableError):
            raise exc
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
