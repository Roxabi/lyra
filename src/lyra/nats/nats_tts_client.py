"""NatsTtsClient — hub-side NATS request-reply client for TTS.

Maintains a ``WorkerRegistry`` populated from heartbeats, and routes each
synthesis to workers in score order via their per-worker subject
``lyra.voice.tts.request.{worker_id}``. Walks the registry on timeout or
NoResponders, marking stale workers and continuing to the next candidate.
"""

from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, NoReturn
from uuid import uuid4

from nats.aio.client import Client as NATS
from nats.errors import NoRespondersError
from pydantic import ValidationError

from lyra.nats.worker_registry import WorkerRegistry
from lyra.tts import SynthesisResult, TtsUnavailableError
from roxabi_contracts.envelope import CONTRACT_VERSION
from roxabi_contracts.voice import (
    SUBJECTS,
    TtsRequest,
    TtsResponse,
    per_worker_tts,
    validate_worker_id,
)
from roxabi_nats._tts_constants import _TTS_CONFIG_FIELDS
from roxabi_nats.circuit_breaker import NatsCircuitBreaker

if TYPE_CHECKING:
    from lyra.core.agent.agent_config import AgentTTSConfig

log = logging.getLogger(__name__)

_TTS_TIMEOUT_DEFAULT = 30.0
_TTS_TIMEOUT_MIN = 1.0
_TTS_TIMEOUT_MAX = 300.0


def _parse_tts_timeout(timeout: float | None) -> float:
    """Resolve TTS timeout: explicit arg > LYRA_TTS_TIMEOUT env var > 30s default.

    Accepts values in [1.0, 300.0] seconds; falls back to 30s on invalid input.
    """
    import os

    if timeout is not None:
        value = timeout
    else:
        raw = os.environ.get("LYRA_TTS_TIMEOUT", str(_TTS_TIMEOUT_DEFAULT))
        try:
            value = float(raw)
        except ValueError:
            log.warning(
                "LYRA_TTS_TIMEOUT=%r is not a valid float; using %.0fs",
                raw,
                _TTS_TIMEOUT_DEFAULT,
            )
            return _TTS_TIMEOUT_DEFAULT
    if not (_TTS_TIMEOUT_MIN <= value <= _TTS_TIMEOUT_MAX):
        log.warning(
            "TTS timeout %.1fs out of range [%.0f, %.0f]; using %.0fs",
            value,
            _TTS_TIMEOUT_MIN,
            _TTS_TIMEOUT_MAX,
            _TTS_TIMEOUT_DEFAULT,
        )
        return _TTS_TIMEOUT_DEFAULT
    return value


def _is_no_responders(exc: Exception) -> bool:
    """Detect NATS NoRespondersError."""
    return isinstance(exc, NoRespondersError)


class NatsTtsClient:
    def __init__(self, nc: NATS, *, timeout: float | None = None) -> None:
        self._nc = nc
        self._timeout = _parse_tts_timeout(timeout)
        self._cb = NatsCircuitBreaker()
        self._registry = WorkerRegistry()
        self._hb_sub = None  # set by start

    async def start(self) -> None:
        """Subscribe to heartbeat subject. Called once after nc is connected."""
        if self._hb_sub is None:
            self._hb_sub = await self._nc.subscribe(
                SUBJECTS.tts_heartbeat, cb=self._on_heartbeat
            )

    async def stop(self) -> None:
        """Unsubscribe from heartbeat subject. Idempotent."""
        if self._hb_sub is not None:
            await self._hb_sub.unsubscribe()
            self._hb_sub = None
            log.debug("NatsTtsClient stopped")

    def is_available(self) -> bool:
        """Check if any TTS workers are registered via heartbeats."""
        return self._registry.any_alive()

    async def _on_heartbeat(self, msg) -> None:
        try:
            data = json.loads(msg.data)
        except json.JSONDecodeError:
            log.debug("tts_client: heartbeat parse error", exc_info=True)
            return
        worker_id = data.get("worker_id")
        if not worker_id:
            log.warning("tts_client: heartbeat missing worker_id, ignoring")
            return
        if not isinstance(worker_id, str):
            log.warning(
                "tts_client: heartbeat non-string worker_id=%r, ignoring", worker_id
            )
            return
        # Receive-side match for the PUBLISH-path safe-chars enforcement in
        # per_worker_tts. Without this, a rogue worker publishing a heartbeat
        # with a wildcard-bearing id (e.g. "evil.worker.*") would pollute the
        # registry until first routing attempt; the publish helper would then
        # raise at call time, long after the trust boundary was crossed.
        try:
            validate_worker_id(worker_id)
        except ValueError:
            log.warning(
                "tts_client: heartbeat with unsafe worker_id=%r, ignoring",
                worker_id,
            )
            return
        self._registry.record_heartbeat(data)

    def _parse_reply(self, raw: bytes) -> TtsResponse:
        """Validate a NATS reply against TtsResponse; translate a ValidationError
        into TtsUnavailableError + record a circuit-breaker failure."""
        try:
            return TtsResponse.model_validate_json(raw)
        except ValidationError as exc:
            self._cb.record_failure()
            raise TtsUnavailableError("TTS reply failed schema validation") from exc

    async def _walk_registry(self, payload: bytes) -> TtsResponse:
        """Walk workers in score order, trying each until success or exhaustion.

        Replaces _send/_fallback with registry-authoritative routing:
        - Empty registry → TtsUnavailableError immediately
        - Timeout/NoResponders → mark worker stale, continue to next
        - Other exceptions → terminal failure (no retry)
        - All exhausted → single CB failure + TtsUnavailableError chained from last
        """
        payload_kb = len(payload) / 1024
        candidates = self._registry.ordered_by_score()
        if not candidates:
            raise TtsUnavailableError("TTS: no live worker (heartbeat stale >15s)")

        last_exc: Exception | None = None
        for worker in candidates:
            target = per_worker_tts(worker.worker_id)
            try:
                reply = await self._nc.request(target, payload, timeout=self._timeout)
                resp = self._parse_reply(reply.data)
                if not resp.ok:
                    self._cb.record_failure()
                    raise TtsUnavailableError(resp.error or "TTS synthesis failed")
                return resp
            except TimeoutError as exc:
                self._registry.mark_stale(worker.worker_id)
                last_exc = exc
                continue
            except Exception as exc:
                if _is_no_responders(exc):
                    self._registry.mark_stale(worker.worker_id)
                    last_exc = exc
                    continue
                # Terminal failure — propagate via standard error boundary
                self._raise_nats_failure(exc, payload_kb)

        # All workers exhausted
        log.warning(
            "TTS: all workers unresponsive, last error type=%s",
            type(last_exc).__name__ if last_exc else "None",
        )
        self._cb.record_failure()
        raise TtsUnavailableError("TTS: all workers unresponsive") from last_exc

    def _raise_nats_failure(self, exc: Exception, payload_kb: float) -> NoReturn:
        """Convert a NATS request exception to TtsUnavailableError.

        Domain errors (``TtsUnavailableError``) pass through unchanged so callers
        can rely on this being the single translation boundary for NATS-transport
        exceptions — no per-site ``except TtsUnavailableError: raise`` guard
        needed anywhere in this file.
        """
        if isinstance(exc, TtsUnavailableError):
            raise exc
        if "max_payload" in str(exc).lower() or "MaxPayload" in type(exc).__name__:
            log.error(
                "TTS payload too large (%.0f KB) — check NATS max_payload",
                payload_kb,
            )
            self._cb.record_failure()
            raise TtsUnavailableError("TTS request payload too large") from exc
        log.warning("TTS adapter unreachable: %s: %s", type(exc).__name__, exc)
        self._cb.record_failure()
        raise TtsUnavailableError("TTS adapter unreachable") from exc

    async def synthesize(
        self,
        text: str,
        *,
        agent_tts: "AgentTTSConfig | None" = None,
        language: str | None = None,
        voice: str | None = None,
        fallback_language: str | None = None,
    ) -> SynthesisResult:
        if self._cb.is_open():
            raise TtsUnavailableError(
                "TTS circuit open — adapter temporarily unavailable"
            )
        req_kwargs: dict[str, Any] = {
            "contract_version": CONTRACT_VERSION,
            "trace_id": str(uuid4()),
            "issued_at": datetime.now(timezone.utc),
            "request_id": str(uuid4()),
            "text": text,
            "language": language,
            "voice": voice,
            "fallback_language": fallback_language,
            "chunked": True,
        }
        if agent_tts is not None:
            # voice/language use caller-override semantics (see below); excluded here
            for field in _TTS_CONFIG_FIELDS:
                val = getattr(agent_tts, field, None)
                if val is not None:
                    req_kwargs[field] = val
            # Also pass language/voice from agent_tts if not overridden by caller
            if language is None and getattr(agent_tts, "language", None) is not None:
                req_kwargs["language"] = agent_tts.language
            if voice is None and getattr(agent_tts, "voice", None) is not None:
                req_kwargs["voice"] = agent_tts.voice
        request = TtsRequest.model_validate(req_kwargs)
        payload = request.model_dump_json(exclude_none=True).encode("utf-8")
        resp = await self._walk_registry(payload)
        # TtsResponse._enforce_success_invariant guarantees audio_b64, mime_type,
        # and duration_ms are non-null whenever ok=True. Asserting narrows the
        # types for the type checker and fails loudly if that invariant ever drifts.
        assert resp.audio_b64 is not None
        assert resp.mime_type is not None
        assert resp.duration_ms is not None
        audio_bytes = base64.b64decode(resp.audio_b64)
        self._cb.record_success()
        return SynthesisResult(
            audio_bytes=audio_bytes,
            mime_type=resp.mime_type,
            duration_ms=resp.duration_ms,
            waveform_b64=resp.waveform_b64,
        )
