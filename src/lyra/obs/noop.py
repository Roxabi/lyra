"""NoOpObsProvider — default when observability is disabled."""

from __future__ import annotations

from typing import Any

from lyra.obs.base import ObsSpan, ObsTrace


class NoOpObsProvider:
    capabilities: dict[str, Any] = {"backend": "noop"}

    def start_trace(
        self, trace_id: str, session_id: str, name: str, **kwargs: Any
    ) -> ObsTrace:
        return ObsTrace(trace_id=trace_id, session_id=session_id)

    def end_trace(self, trace: ObsTrace, *, error: str | None = None) -> None:
        pass

    def start_span(
        self, trace: ObsTrace, name: str, **kwargs: Any
    ) -> ObsSpan:
        return ObsSpan(span_id="noop", trace_id=trace.trace_id, name=name)

    def end_span(self, span: ObsSpan, *, error: str | None = None) -> None:
        pass

    def record_generation(
        self,
        span: ObsSpan,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: float,
        **kwargs: Any,
    ) -> None:
        pass

    def record_event(
        self,
        span: ObsSpan,
        name: str,
        *,
        input: Any = None,
        output: Any = None,
        **kwargs: Any,
    ) -> None:
        pass
