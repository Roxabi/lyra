"""Tests for ObservabilityProvider protocol and NoOpObsProvider."""

from __future__ import annotations

import pytest

from lyra.obs import NoOpObsProvider, ObservabilityProvider, ObsSpan, ObsTrace

# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------


def test_noop_implements_protocol() -> None:
    provider = NoOpObsProvider()
    assert isinstance(provider, ObservabilityProvider)


# ---------------------------------------------------------------------------
# ObsTrace / ObsSpan dataclass fields
# ---------------------------------------------------------------------------


def test_obs_trace_fields() -> None:
    trace = ObsTrace(trace_id="t-1", session_id="s-1", name="my-trace")
    assert trace.trace_id == "t-1"
    assert trace.session_id == "s-1"
    assert trace.name == "my-trace"
    assert trace.metadata == {}


def test_obs_trace_with_metadata() -> None:
    trace = ObsTrace(
        trace_id="t-2", session_id="s-2", name="meta-trace", metadata={"key": "val"}
    )
    assert trace.metadata == {"key": "val"}


def test_obs_span_fields() -> None:
    span = ObsSpan(span_id="sp-1", trace_id="t-1", name="my-span")
    assert span.span_id == "sp-1"
    assert span.trace_id == "t-1"
    assert span.name == "my-span"
    assert span.metadata == {}


def test_obs_span_with_metadata() -> None:
    span = ObsSpan(span_id="sp-2", trace_id="t-1", name="span", metadata={"x": 1})
    assert span.metadata == {"x": 1}


# ---------------------------------------------------------------------------
# NoOpObsProvider — all methods callable without error
# ---------------------------------------------------------------------------


@pytest.fixture()
def provider() -> NoOpObsProvider:
    return NoOpObsProvider()


@pytest.fixture()
def trace(provider: NoOpObsProvider) -> ObsTrace:
    return provider.start_trace(trace_id="t-1", session_id="s-1", name="test-trace")


@pytest.fixture()
def span(provider: NoOpObsProvider, trace: ObsTrace) -> ObsSpan:
    return provider.start_span(trace, name="test-span")


def test_capabilities(provider: NoOpObsProvider) -> None:
    assert provider.capabilities == {"backend": "noop"}


def test_start_trace_returns_obs_trace(trace: ObsTrace) -> None:
    assert isinstance(trace, ObsTrace)
    assert trace.trace_id == "t-1"
    assert trace.session_id == "s-1"
    assert trace.name == "test-trace"


def test_end_trace_no_error(provider: NoOpObsProvider, trace: ObsTrace) -> None:
    provider.end_trace(trace)  # must not raise


def test_end_trace_with_error(provider: NoOpObsProvider, trace: ObsTrace) -> None:
    provider.end_trace(
        trace, error=ValueError("something went wrong")
    )  # must not raise


def test_start_span_returns_obs_span(span: ObsSpan) -> None:
    assert isinstance(span, ObsSpan)
    assert span.span_id == "noop"
    assert span.name == "test-span"


def test_end_span_no_error(provider: NoOpObsProvider, span: ObsSpan) -> None:
    provider.end_span(span)  # must not raise


def test_end_span_with_error(provider: NoOpObsProvider, span: ObsSpan) -> None:
    provider.end_span(span, error=ValueError("span error"))  # must not raise


def test_record_generation(provider: NoOpObsProvider, span: ObsSpan) -> None:
    provider.record_generation(
        span,
        model="gpt-4o",
        input_tokens=100,
        output_tokens=50,
        latency_ms=123.4,
    )  # must not raise


def test_record_generation_with_extra_kwargs(
    provider: NoOpObsProvider, span: ObsSpan
) -> None:
    provider.record_generation(
        span,
        model="claude-3",
        input_tokens=200,
        output_tokens=80,
        latency_ms=99.9,
        cost_usd=0.002,
    )  # must not raise


def test_record_event_no_io(provider: NoOpObsProvider, span: ObsSpan) -> None:
    provider.record_event(span, "tool_call")  # must not raise


def test_record_event_with_io(provider: NoOpObsProvider, span: ObsSpan) -> None:
    provider.record_event(
        span,
        "tool_call",
        input_data={"arg": "value"},
        output_data={"result": 42},
    )  # must not raise


def test_record_event_with_extra_kwargs(
    provider: NoOpObsProvider, span: ObsSpan
) -> None:
    provider.record_event(span, "custom", level="debug")  # must not raise
