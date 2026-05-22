"""LlmClient — thin domain client over WorkerPoolClient + LlmCodec.

3-layer composition (spec § Slice S4):
  LlmClient → WorkerPoolClient → NatsTransport (or HttpTransport P4)

Implements the LlmProvider protocol over Result[T, SanitizedError] transport.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, AsyncIterator

from lyra.core.messaging.events import LlmEvent, ResultLlmEvent
from lyra.core.ports.llm import LlmResult
from roxabi_contracts.llm import SUBJECTS

if TYPE_CHECKING:
    from lyra.core.agent.agent_config import ModelConfig
    from lyra.llm.codec import LlmCodec
    from lyra.transport.worker_pool_client import WorkerPoolClient


class LlmClient:
    capabilities = {"streaming": True, "auth": "nats"}

    def __init__(
        self,
        pool: "WorkerPoolClient",
        codec: "LlmCodec",
        *,
        timeout: float | None = None,
    ) -> None:
        self._pool = pool
        self._codec = codec
        self._timeout = timeout

    def is_alive(self, pool_id: str) -> bool:
        del pool_id
        return self._pool.is_pool_alive()

    async def stop(self) -> None:
        """Stop heartbeat subscription on the underlying pool."""
        await self._pool.stop()

    async def complete(  # noqa: PLR0913 — LlmProvider protocol signature
        self,
        pool_id: str,
        text: str,
        model_cfg: "ModelConfig",
        system_prompt: str,
        *,
        messages: list[dict] | None = None,
    ) -> LlmResult:
        del pool_id
        payload, trace_id = self._codec.encode(
            text, model_cfg, system_prompt, messages, stream=False
        )
        result = await self._pool.request_with_routing(
            lambda _: SUBJECTS.generate_request,
            payload,
            max_attempts=1,
            timeout=self._timeout,
        )
        return self._codec.decode(result, trace_id)

    async def stream(  # noqa: PLR0913 — LlmProvider protocol signature
        self,
        pool_id: str,
        text: str,
        model_cfg: "ModelConfig",
        system_prompt: str,
        *,
        messages: list[dict] | None = None,
    ) -> AsyncIterator[LlmEvent]:
        del pool_id
        payload, _ = self._codec.encode(
            text, model_cfg, system_prompt, messages, stream=True
        )
        async for result in self._pool.stream_request(
            SUBJECTS.generate_request, payload, timeout=self._timeout
        ):
            event = self._codec.decode_chunk(result)
            if event is None:
                continue
            yield event
            if isinstance(event, ResultLlmEvent):
                return
