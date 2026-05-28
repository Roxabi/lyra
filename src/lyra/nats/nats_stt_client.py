"""NatsSttClient — thin STT domain client over WorkerPoolClient + SttCodec.

Composition (3-layer): NatsSttClient → WorkerPoolClient → NatsTransport.
Implements STTProtocol. Per-worker routing via roxabi_contracts.voice.per_worker_stt.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from lyra.core.ports.stt import STTNoiseError, STTUnavailableError, TranscriptionResult
from lyra.nats.nats_stt_codec import SttEncodeParams
from lyra.nats.stt_helpers import is_whisper_noise
from roxabi_contracts import PENDING_STORE_KEY, BlobRef
from roxabi_contracts.voice import per_worker_stt

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS

    from lyra.nats.nats_stt_codec import SttCodec
    from lyra.transport.worker_pool_client import WorkerPoolClient

log = logging.getLogger(__name__)


class NatsSttClient:
    def __init__(
        self,
        pool: "WorkerPoolClient",
        codec: "SttCodec",
        *,
        model: str = "large-v3-turbo",
        nc: "NATS | None" = None,
    ) -> None:
        self._pool = pool
        self._codec = codec
        self._model = model
        self._nc = nc

    async def start(self) -> None:
        """Start heartbeat subscription. nc must have been provided at __init__."""
        if self._nc is None:
            raise RuntimeError("NatsSttClient.start() called without nc at __init__")
        await self._pool.start(self._nc)

    async def stop(self) -> None:
        await self._pool.stop()

    def is_available(self) -> bool:
        return self._pool.is_pool_alive()

    async def transcribe(
        self, audio: BlobRef | bytes, mime: str
    ) -> TranscriptionResult:
        params = SttEncodeParams(model=self._model)
        if isinstance(audio, bytes):
            blob_ref = BlobRef(
                store_key=PENDING_STORE_KEY,
                content_hash="",
                mime=mime,
                size=len(audio),
                source="lyra-hub",
            )
        else:
            blob_ref = audio
        payload = self._codec.encode(blob_ref, mime, params)
        result = await self._pool.request_with_routing(
            per_worker_stt, payload, max_attempts=None
        )
        tr = self._codec.decode(result)
        if tr.error:
            raise STTUnavailableError(tr.error)
        if is_whisper_noise(tr.text):
            log.info("STT noise result via NATS: text=%r lang=%s", tr.text, tr.language)
            raise STTNoiseError(f"Noise transcript: {tr.text!r}")
        return tr
