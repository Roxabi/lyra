"""NatsTtsClient — hub-side NATS request-reply client for TTS."""
from __future__ import annotations

import base64
import json
import logging
import time
from typing import TYPE_CHECKING
from uuid import uuid4

from nats.aio.client import Client as NATS

from lyra.nats._tts_constants import _TTS_CONFIG_FIELDS
from lyra.nats.circuit_breaker import NatsCircuitBreaker
from lyra.tts import SynthesisResult, TtsUnavailableError

if TYPE_CHECKING:
    from lyra.core.agent_config import AgentTTSConfig

log = logging.getLogger(__name__)

_HB_SUBJECT = "lyra.voice.tts.heartbeat"
_HB_TTL = 15.0


class NatsTtsClient:
    SUBJECT = "lyra.voice.tts.request"

    def __init__(self, nc: NATS, *, timeout: float = 30.0) -> None:
        self._nc = nc
        self._timeout = timeout
        self._cb = NatsCircuitBreaker()
        self._worker_freshness: dict[str, float] = {}
        self._hb_sub = None  # set by start

    async def start(self) -> None:
        """Subscribe to heartbeat subject. Called once after nc is connected."""
        if self._hb_sub is None:
            self._hb_sub = await self._nc.subscribe(
                _HB_SUBJECT, cb=self._on_heartbeat
            )

    async def _on_heartbeat(self, msg) -> None:
        try:
            data = json.loads(msg.data)
            worker_id = data.get("worker_id")
            if not worker_id:
                log.warning("tts_client: heartbeat missing worker_id, ignoring")
                return
            self._worker_freshness[worker_id] = time.monotonic()
        except Exception:
            log.debug("tts_client: heartbeat parse error", exc_info=True)

    def _any_worker_alive(self) -> bool:
        now = time.monotonic()
        self._worker_freshness = {
            k: v for k, v in self._worker_freshness.items() if now - v <= _HB_TTL * 2
        }
        return any(now - ts <= _HB_TTL for ts in self._worker_freshness.values())

    async def _send(self, payload: bytes, payload_kb: float) -> dict:
        """Send payload to TTS subject and return parsed response dict."""
        try:
            reply = await self._nc.request(self.SUBJECT, payload, timeout=self._timeout)
            data = json.loads(reply.data)
        except TimeoutError as exc:
            log.warning("TTS adapter timeout after %.0fs", self._timeout)
            self._cb.record_failure()
            raise TtsUnavailableError("TTS adapter timeout") from exc
        except Exception as exc:
            if "max_payload" in str(exc).lower() or "MaxPayload" in type(exc).__name__:
                log.error(
                    "TTS payload too large (%.0f KB)"
                    " — check NATS max_payload",
                    payload_kb,
                )
                self._cb.record_failure()
                raise TtsUnavailableError("TTS request payload too large") from exc
            log.warning("TTS adapter unreachable: %s: %s", type(exc).__name__, exc)
            self._cb.record_failure()
            raise TtsUnavailableError("TTS adapter unreachable") from exc
        if not data.get("ok"):
            self._cb.record_failure()
            raise TtsUnavailableError("TTS synthesis failed")
        return data

    async def synthesize(
        self,
        text: str,
        *,
        agent_tts: "AgentTTSConfig | None" = None,
        language: str | None = None,
        voice: str | None = None,
        fallback_language: str | None = None,
    ) -> SynthesisResult:
        if not self._any_worker_alive():
            raise TtsUnavailableError(
                "TTS: no live worker (heartbeat stale >15s)"
            )
        if self._cb.is_open():
            raise TtsUnavailableError(
                "TTS circuit open — adapter temporarily unavailable"
            )
        request: dict = {
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
                    request[field] = val
            # Also pass language/voice from agent_tts if not overridden by caller
            if language is None and getattr(agent_tts, "language", None) is not None:
                request["language"] = agent_tts.language
            if voice is None and getattr(agent_tts, "voice", None) is not None:
                request["voice"] = agent_tts.voice
        payload = json.dumps(request, ensure_ascii=False).encode("utf-8")
        data = await self._send(payload, len(payload) / 1024)
        audio_bytes = base64.b64decode(data["audio_b64"])
        self._cb.record_success()
        return SynthesisResult(
            audio_bytes=audio_bytes,
            mime_type=data.get("mime_type", "audio/ogg"),
            duration_ms=data.get("duration_ms"),
            waveform_b64=data.get("waveform_b64"),
        )
