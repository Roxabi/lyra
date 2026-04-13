"""ObservabilityProvider — abstraction over OTel / Langfuse / NoOp backends."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, TypedDict, Unpack, runtime_checkable


@dataclass(frozen=True)
class ObsTrace:
    """Handle for a root trace.

    `session_id` correlates spans across a user session. It must be an opaque,
    non-personally-identifiable token (e.g. HMAC of the real platform ID) —
    never the raw Telegram/Discord user ID — since it is forwarded verbatim to
    external observability backends.
    """

    trace_id: str
    session_id: str
    name: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ObsSpan:
    span_id: str
    trace_id: str
    name: str
    parent_span_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ObsCapabilities:
    """Declared capabilities of an ObservabilityProvider implementation."""

    backend: str = "unknown"
    has_tracing: bool = False
    has_spans: bool = False
    has_generation_recording: bool = False
    has_event_recording: bool = False
    async_flush: bool = False


class GenerationKwargs(TypedDict, total=False):
    """Optional typed kwargs for record_generation."""

    cost_usd: float
    model_version: str
    cached: bool
    finish_reason: str
    prompt_tokens_cached: int


@runtime_checkable
class ObservabilityProvider(Protocol):
    """Protocol for observability backends (OTel, Langfuse, NoOp).

    PII & secrets contract:
    - Callers MUST scrub PII, credentials, and raw user content before
      passing values via `metadata`, `input_data`, `output_data`, or `error`.
    - Backend implementations MUST NOT log these fields at INFO level or
      above without an explicit opt-in.
    - `error` should carry the exception itself (not a raw stringified
      traceback) so backends can extract fields structurally.
    """

    capabilities: ObsCapabilities

    async def start_trace(
        self, trace_id: str, session_id: str, name: str, **kwargs: Any
    ) -> ObsTrace: ...

    async def end_trace(
        self, trace: ObsTrace, *, error: BaseException | None = None
    ) -> None: ...

    async def start_span(
        self,
        trace: ObsTrace,
        name: str,
        *,
        parent_span_id: str | None = None,
        **kwargs: Any,
    ) -> ObsSpan: ...

    async def end_span(
        self, span: ObsSpan, *, error: BaseException | None = None
    ) -> None: ...

    async def record_generation(
        self,
        span: ObsSpan,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: float,
        **kwargs: Unpack[GenerationKwargs],
    ) -> None: ...

    async def record_event(
        self,
        span: ObsSpan,
        name: str,
        *,
        input_data: Any = None,
        output_data: Any = None,
        **kwargs: Any,
    ) -> None:
        """Record a named event on a span.

        `input_data`/`output_data` may contain arbitrary structured payloads
        and will be forwarded to the backend verbatim. Callers are responsible
        for PII/secret scrubbing before invocation.
        """
        ...
