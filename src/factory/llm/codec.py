"""LlmCodec Protocol — encode/decode boundary for LLM domain.

Concrete impls: CliNatsCodec (NATS path) ; future HTTP codecs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from factory.core.agent.agent_config import ModelConfig
    from factory.core.messaging.events import LlmEvent
    from factory.core.ports.llm import LlmResult
    from factory.transport._result import Result, SanitizedError
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
        **kwargs: Any,
    ) -> tuple[bytes, str]: ...

    def decode(
        self, result: "Result[bytes, SanitizedError]", trace_id: str
    ) -> "LlmResult": ...

    def decode_chunk(
        self, result: "Result[bytes, SanitizedError]"
    ) -> "LlmEvent | None": ...

    def encode_control(self, cmd: "CliControlCmd") -> bytes: ...
