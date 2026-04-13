"""NoOpObsProvider — default when observability is disabled."""

from __future__ import annotations

from typing import Any, ClassVar

from lyra.obs.base import ObsSpan, ObsTrace


class NoOpObsProvider:
    capabilities: ClassVar[dict[str, Any]] = {"backend": "noop"}

    def start_trace(
        self, trace_id: str, session_id: str, name: str, **kwargs: Any
    ) -> ObsTrace:
        return ObsTrace(trace_id=trace_id, session_id=session_id, name=name)

    def end_trace(self, trace: ObsTrace, *, error: BaseException | None = None) -> None:
        pass

    def start_span(
        self,
        trace: ObsTrace,
        name: str,
        *,
        parent_span_id: str | None = None,
        **kwargs: Any,
    ) -> ObsSpan:
        return ObsSpan(
            span_id="noop",
            trace_id=trace.trace_id,
            name=name,
            parent_span_id=parent_span_id,
        )

    def end_span(self, span: ObsSpan, *, error: BaseException | None = None) -> None:
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
        input_data: Any = None,
        output_data: Any = None,
        **kwargs: Any,
    ) -> None:
        pass
