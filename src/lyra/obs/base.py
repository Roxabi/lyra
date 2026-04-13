"""ObservabilityProvider — abstraction over OTel / Langfuse / NoOp backends."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class ObsTrace:
    trace_id: str
    session_id: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ObsSpan:
    span_id: str
    trace_id: str
    name: str
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ObservabilityProvider(Protocol):
    capabilities: dict[str, Any]

    def start_trace(
        self, trace_id: str, session_id: str, name: str, **kwargs: Any
    ) -> ObsTrace: ...

    def end_trace(self, trace: ObsTrace, *, error: str | None = None) -> None: ...

    def start_span(self, trace: ObsTrace, name: str, **kwargs: Any) -> ObsSpan: ...

    def end_span(self, span: ObsSpan, *, error: str | None = None) -> None: ...

    def record_generation(
        self,
        span: ObsSpan,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: float,
        **kwargs: Any,
    ) -> None: ...

    def record_event(
        self,
        span: ObsSpan,
        name: str,
        *,
        input: Any = None,
        output: Any = None,
        **kwargs: Any,
    ) -> None: ...
