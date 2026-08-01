"""Memory-domain NATS contract models (roxabi-cortex).

Pure Pydantic. No NATS imports. No transport logic.

MVP surface (vault migration path):
  CaptureRequest/Response  — put knowledge entry (cortex; hub /vault-add gone)
  SearchRequest/Response   — full-text search
  AssembleRequest/Response — long-term memory block for agent context
"""

from __future__ import annotations

from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from roxabi_contracts.envelope import WorkEnvelope
from roxabi_contracts.errors import WorkerError

RequestId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,128}$")]
SafeSlug = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=64,
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$",
    ),
]


class CaptureRequest(WorkEnvelope):
    """Persist a knowledge entry. Canonical subject: ``roxabi.memory.capture``."""

    request_id: RequestId
    title: Annotated[str, StringConstraints(min_length=1, max_length=512)]
    body: Annotated[str, StringConstraints(min_length=0, max_length=500_000)]
    category: SafeSlug = "references"
    entry_type: SafeSlug = "bookmark"
    url: str = ""
    tags: list[str] = Field(default_factory=list)
    namespace: SafeSlug = "vault"
    metadata: dict = Field(default_factory=dict)


class CaptureResponse(WorkEnvelope):
    """Capture result."""

    ok: bool
    request_id: RequestId
    entry_id: int | None = None
    error: str | None = None
    worker_error: WorkerError | None = None
    duration_ms: int | None = None

    @model_validator(mode="after")
    def _ok_requires_entry_id(self) -> Self:
        if self.ok and self.entry_id is None:
            raise ValueError("CaptureResponse with ok=True must carry entry_id")
        return self


class SearchRequest(WorkEnvelope):
    """Full-text search. Canonical subject: ``roxabi.memory.query.search``."""

    request_id: RequestId
    query: Annotated[str, StringConstraints(min_length=1, max_length=1024)]
    namespace: SafeSlug | None = None
    category: SafeSlug | None = None
    limit: int = Field(default=20, ge=1, le=100)


class SearchHit(BaseModel):
    """One search result row (nested payload — not a wire envelope)."""

    model_config = ConfigDict(extra="ignore")

    entry_id: int
    title: str
    category: str
    entry_type: str
    snippet: str = ""
    score: float | None = None
    url: str = ""


class SearchResponse(WorkEnvelope):
    """Search result list. Failures set ok=False; empty hits are still ok=True."""

    ok: bool
    request_id: RequestId
    hits: list[SearchHit] = Field(default_factory=list)
    error: str | None = None
    worker_error: WorkerError | None = None
    duration_ms: int | None = None


class AssembleRequest(WorkEnvelope):
    """Build a context block for agent recall.

    Canonical subject: ``roxabi.memory.query.assemble`` (ADR-087).
    """

    request_id: RequestId
    goal: str | None = None
    budget_tokens: int = Field(default=4000, ge=64, le=32_000)
    namespace: SafeSlug | None = None
    user_id: str | None = None
    fresh_tail_days: int = Field(default=7, ge=0, le=365)


class AssembleItem(BaseModel):
    """One assembled context fragment (nested payload — not a wire envelope)."""

    model_config = ConfigDict(extra="ignore")

    kind: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    content: Annotated[str, StringConstraints(min_length=0, max_length=100_000)]
    tokens: int = Field(default=0, ge=0)
    strength: float | None = None
    entry_id: int | None = None


class AssembleResponse(WorkEnvelope):
    """Assembled memory block. ok=True with empty items is valid (no memory)."""

    ok: bool
    request_id: RequestId
    items: list[AssembleItem] = Field(default_factory=list)
    tokens_used: int = Field(default=0, ge=0)
    text: str = ""  # pre-joined block for simple consumers
    goal: str | None = None
    error: str | None = None
    worker_error: WorkerError | None = None
    duration_ms: int | None = None
