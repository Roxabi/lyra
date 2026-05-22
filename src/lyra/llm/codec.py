"""LlmCodec Protocol — encode/decode boundary for LLM domain.

Concrete impls: CliNatsCodec (NATS path) ; future HTTP codecs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from lyra.core.agent.agent_config import ModelConfig
    from lyra.core.messaging.events import LlmEvent
    from lyra.core.ports.llm import LlmResult
    from lyra.transport._result import Result, SanitizedError
    from roxabi_contracts.cli.models import CliControlCmd


@runtime_checkable
class LlmCodec(Protocol):
    def encode(
        self,
        text: str,
        model_cfg: "ModelConfig",
        system_prompt: str,
        messages: list[dict] | None,
        *,
        stream: bool,
    ) -> tuple[bytes, str]: ...

    def decode(
        self, result: "Result[bytes, SanitizedError]", trace_id: str
    ) -> "LlmResult": ...

    def decode_chunk(
        self, result: "Result[bytes, SanitizedError]"
    ) -> "LlmEvent | None": ...

    def encode_control(self, cmd: "CliControlCmd") -> bytes: ...

    def set_session_store(self, store) -> None: ...
