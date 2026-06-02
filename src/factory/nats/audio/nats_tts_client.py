"""NatsTtsClient — thin TTS domain client over WorkerPoolClient + TtsCodec.

Composition (3-layer): NatsTtsClient → WorkerPoolClient → NatsTransport.
Implements TtsProtocol. Per-worker routing via roxabi_contracts.voice.per_worker_tts.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from factory.core.ports.tts import (
    SynthesisResult,
    TtsSynthesisError,
    TtsUnavailableError,
)
from roxabi_contracts.voice import per_worker_tts

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS

    from factory.core.agent.agent_config import AgentTTSConfig
    from factory.nats.audio.nats_tts_codec import TtsCodec
    from factory.transport.worker_pool_client import WorkerPoolClient

log = logging.getLogger(__name__)


class NatsTtsClient:
    def __init__(
        self, pool: "WorkerPoolClient", codec: "TtsCodec", nc: "NATS | None" = None
    ) -> None:
        self._pool = pool
        self._codec = codec
        self._nc = nc

    async def start(self) -> None:
        """Start heartbeat subscription. nc must have been provided at __init__."""
        if self._nc is None:
            raise RuntimeError("NatsTtsClient.start() called without nc at __init__")
        await self._pool.start(self._nc)

    async def stop(self) -> None:
        await self._pool.stop()

    def is_available(self) -> bool:
        return self._pool.is_pool_alive()

    async def synthesize(
        self,
        text: str,
        *,
        agent_tts: "AgentTTSConfig | None" = None,
        language: str | None = None,
        voice: str | None = None,
        fallback_language: str | None = None,
    ) -> SynthesisResult:
        payload = self._codec.encode(
            text,
            agent_tts=agent_tts,
            language=language,
            voice=voice,
            fallback_language=fallback_language,
        )
        result = await self._pool.request_with_routing(
            per_worker_tts, payload, max_attempts=None
        )
        synth = self._codec.decode(result)
        if synth.error and synth.unavailable:
            raise TtsUnavailableError(synth.error_message or synth.error)
        elif synth.error:
            raise TtsSynthesisError(
                code=synth.error,
                message=synth.error_message or synth.error,
                detail=synth.error_detail,
                retryable=synth.retryable,
            )
        return synth
