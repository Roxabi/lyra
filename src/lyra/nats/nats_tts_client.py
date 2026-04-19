"""NatsTtsClient — hub-side NATS request-reply client for TTS.

Maintains a ``VoiceWorkerRegistry`` populated from heartbeats, and routes each
synthesis to the least-loaded worker via its per-worker subject
``lyra.voice.tts.request.{worker_id}``. Falls back once to the queue-group
subject (``lyra.voice.tts.request``) if the targeted worker times out.
"""

from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, NoReturn
from uuid import uuid4

from nats.aio.client import Client as NATS
from pydantic import ValidationError

from lyra.nats.voice_health import VoiceWorkerRegistry
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
    from lyra.core.agent_config import AgentTTSConfig

log = logging.getLogger(__name__)


class NatsTtsClient:
    def __init__(self, nc: NATS, *, timeout: float = 30.0) -> None:
        self._nc = nc
        self._timeout = timeout
        self._cb = NatsCircuitBreaker()
        self._registry = VoiceWorkerRegistry()
        self._hb_sub = None  # set by start

    async def start(self) -> None:
        """Subscribe to heartbeat subject. Called once after nc is connected."""
        if self._hb_sub is None:
            self._hb_sub = await self._nc.subscribe(
                SUBJECTS.tts_heartbeat, cb=self._on_heartbeat
            )

    async def _on_heartbeat(self, msg) -> None:
        try:
            data = json.loads(msg.data)
        except Exception:
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

    async def _send(self, payload: bytes, worker_id: str) -> TtsResponse:
        """Send payload to the per-worker subject, falling back once to queue group."""
        payload_kb = len(payload) / 1024
        target = per_worker_tts(worker_id)
        try:
            reply = await self._nc.request(target, payload, timeout=self._timeout)
            resp = self._parse_reply(reply.data)
        except TimeoutError:
            log.warning(
                "TTS: preferred worker %s timed out after %.0fs;"
                " falling back to queue group",
                worker_id,
                self._timeout,
            )
            resp = await self._fallback(payload, payload_kb)
        except Exception as exc:
            self._raise_nats_failure(exc, payload_kb)
        if not resp.ok:
            self._cb.record_failure()
            raise TtsUnavailableError(resp.error or "TTS synthesis failed")
        return resp

    async def _fallback(self, payload: bytes, payload_kb: float) -> TtsResponse:
        try:
            reply = await self._nc.request(
                SUBJECTS.tts_request, payload, timeout=self._timeout
            )
            return self._parse_reply(reply.data)
        except TimeoutError as exc:
            log.warning("TTS adapter timeout after %.0fs", self._timeout)
            self._cb.record_failure()
            raise TtsUnavailableError("TTS adapter timeout") from exc
        except Exception as exc:
            self._raise_nats_failure(exc, payload_kb)

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
        preferred = self._registry.pick_least_loaded()
        if preferred is None:
            raise TtsUnavailableError("TTS: no live worker (heartbeat stale >15s)")
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
        resp = await self._send(payload, preferred.worker_id)
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
