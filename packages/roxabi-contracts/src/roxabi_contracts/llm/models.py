"""LLM-domain NATS contract models.

Pure Pydantic. No NATS imports. No transport logic.

Three envelope models:
  LlmRequest    — hub → worker (generate_request subject)
  LlmChunkEvent — worker → hub (streaming chunks, published to reply inbox)
  LlmResponse   — worker → hub (non-streaming reply, published to reply inbox)
"""

from __future__ import annotations

from typing import Annotated, Literal, Optional, Self

from pydantic import Field, StringConstraints, model_validator

from roxabi_contracts.envelope import ContractEnvelope
from roxabi_contracts.errors import WorkerError


class LlmRequest(ContractEnvelope):
    """LLM generation request. Canonical subject: ``lyra.llm.generate.request``."""

    request_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,128}$")]
    messages: list[dict]
    model: str | None = None
    system_prompt: str | None = None
    stream: bool = True
    max_tokens: int | None = None
    temperature: float | None = None


class LlmChunkEvent(ContractEnvelope):
    """Streaming chunk event. Published per SSE token to the reply inbox.

    Terminal chunk: ``done=True``, ``delta=None``, ``duration_ms`` set.
    Error chunk: ``is_error=True``, ``error`` set, ``done=True``.
    """

    request_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,128}$")]
    delta: str | None = None
    done: bool = False
    is_error: bool = False
    error: str | None = None
    duration_ms: int | None = None
    worker_error: WorkerError | None = None


class LlmResponse(ContractEnvelope):
    """Non-streaming generation response.

    Success-path invariant (enforced by ``_enforce_success_invariant``):
    when ``ok=True``, ``text`` is non-null.
    """

    ok: bool
    request_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,128}$")]
    text: str | None = None
    error: str | None = None
    duration_ms: int | None = None
    worker_error: WorkerError | None = None

    @model_validator(mode="after")
    def _enforce_success_invariant(self) -> Self:
        if self.ok and self.text is None:
            raise ValueError("LlmResponse with ok=True must carry text")
        return self


class LifecycleRequest(ContractEnvelope):
    """Lifecycle control request. Canonical subjects: ``lyra.llm.lifecycle.*``."""

    request_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,128}$")]
    host: Optional[str] = Field(None, pattern=r"^[A-Za-z0-9._-]{0,253}$")
    op: Literal["swap", "stop", "status", "list", "reload-catalog"]
    model_name: Optional[str] = None

    @model_validator(mode="after")
    def _swap_requires_model(self) -> Self:
        if self.op == "swap" and not self.model_name:
            raise ValueError("op='swap' requires model_name")
        return self


class LifecycleResponse(ContractEnvelope):
    """Lifecycle control response. Published to the operator reply inbox."""

    request_id: str
    ok: bool
    host: Optional[str] = None
    error: Optional[str] = None
    worker_error: Optional[WorkerError] = None
    data: Optional[dict] = None
