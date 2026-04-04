"""NatsTtsClient — hub-side NATS request-reply client for TTS."""
from __future__ import annotations

import base64
import json
import logging
from typing import TYPE_CHECKING
from uuid import uuid4

from nats.aio.client import Client as NATS

from lyra.tts import SynthesisResult, TtsUnavailableError

if TYPE_CHECKING:
    from lyra.core.agent_config import AgentTTSConfig

log = logging.getLogger(__name__)

_TTS_CONFIG_FIELDS = (
    "engine",
    "accent",
    "personality",
    "speed",
    "emotion",
    "exaggeration",
    "cfg_weight",
    "segment_gap",
    "crossfade",
    "chunk_size",
)


class NatsTtsClient:
    SUBJECT = "lyra.voice.tts.request"

    def __init__(self, nc: NATS, *, timeout: float = 30.0) -> None:
        self._nc = nc
        self._timeout = timeout

    async def synthesize(
        self,
        text: str,
        *,
        agent_tts: "AgentTTSConfig | None" = None,
        language: str | None = None,
        voice: str | None = None,
        fallback_language: str | None = None,
    ) -> SynthesisResult:
        request: dict = {
            "request_id": str(uuid4()),
            "text": text,
            "language": language,
            "voice": voice,
            "fallback_language": fallback_language,
            "chunked": True,
        }
        if agent_tts is not None:
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
        payload_kb = len(payload) / 1024
        try:
            reply = await self._nc.request(self.SUBJECT, payload, timeout=self._timeout)
            data = json.loads(reply.data)
        except TimeoutError as exc:
            log.warning("TTS adapter timeout after %.0fs", self._timeout)
            raise TtsUnavailableError("TTS adapter timeout") from exc
        except Exception as exc:
            if "max_payload" in str(exc).lower() or "MaxPayload" in type(exc).__name__:
                log.error(
                    "TTS request payload too large (%.0f KB) — check NATS max_payload config",
                    payload_kb,
                )
                raise TtsUnavailableError("TTS request payload too large") from exc
            log.warning("TTS adapter unreachable: %s: %s", type(exc).__name__, exc)
            raise TtsUnavailableError("TTS adapter unreachable") from exc
        if not data.get("ok"):
            raise TtsUnavailableError("TTS synthesis failed")
        audio_bytes = base64.b64decode(data["audio_b64"])
        return SynthesisResult(
            audio_bytes=audio_bytes,
            mime_type=data.get("mime_type", "audio/ogg"),
            duration_ms=data.get("duration_ms"),
            waveform_b64=data.get("waveform_b64"),
        )
