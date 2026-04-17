"""Tests for ObservabilityProvider protocol and NoOpObsProvider."""

from __future__ import annotations

from typing import Any

import pytest

from lyra.obs import (
    NoOpObsProvider,
    ObsCapabilities,
    ObservabilityProvider,
    ObsSpan,
    ObsTrace,
)

# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------


def test_noop_implements_protocol() -> None:
    # @runtime_checkable verifies method NAMES only, not signatures.
    # Signature conformance is enforced statically by Pyright.
    provider = NoOpObsProvider()
    assert isinstance(provider, ObservabilityProvider)


def test_partial_impl_fails_protocol_check() -> None:
    """A class missing a required method must not satisfy the Protocol."""

    class PartialProvider:
        capabilities = ObsCapabilities(backend="partial")

        async def start_trace(
            self, trace_id: str, session_id: str, name: str, **kwargs: Any
        ) -> ObsTrace:
            return ObsTrace(trace_id=trace_id, session_id=session_id, name=name)

        # Missing end_trace, start_span, end_span, record_generation, record_event

    assert not isinstance(PartialProvider(), ObservabilityProvider)


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
async def trace(provider: NoOpObsProvider) -> ObsTrace:
    return await provider.start_trace(
        trace_id="t-1", session_id="s-1", name="test-trace"
    )


@pytest.fixture()
async def span(provider: NoOpObsProvider, trace: ObsTrace) -> ObsSpan:
    return await provider.start_span(trace, name="test-span")


def test_capabilities(provider: NoOpObsProvider) -> None:
    assert provider.capabilities.backend == "noop"


async def test_start_trace_returns_obs_trace(trace: ObsTrace) -> None:
    assert isinstance(trace, ObsTrace)
    assert trace.trace_id == "t-1"
    assert trace.session_id == "s-1"
    assert trace.name == "test-trace"


async def test_end_trace_no_error(provider: NoOpObsProvider, trace: ObsTrace) -> None:
    await provider.end_trace(trace)  # must not raise


async def test_end_trace_with_error(provider: NoOpObsProvider, trace: ObsTrace) -> None:
    await provider.end_trace(
        trace, error=ValueError("something went wrong")
    )  # must not raise


async def test_start_span_returns_obs_span(span: ObsSpan, trace: ObsTrace) -> None:
    assert isinstance(span, ObsSpan)
    assert span.span_id == "noop"
    assert span.name == "test-span"
    assert span.trace_id == trace.trace_id


async def test_end_span_no_error(provider: NoOpObsProvider, span: ObsSpan) -> None:
    await provider.end_span(span)  # must not raise


async def test_end_span_with_error(provider: NoOpObsProvider, span: ObsSpan) -> None:
    await provider.end_span(span, error=ValueError("span error"))  # must not raise


async def test_record_generation(provider: NoOpObsProvider, span: ObsSpan) -> None:
    await provider.record_generation(
        span,
        model="gpt-4o",
        input_tokens=100,
        output_tokens=50,
        latency_ms=123.4,
    )  # must not raise


async def test_record_generation_with_extra_kwargs(
    provider: NoOpObsProvider, span: ObsSpan
) -> None:
    await provider.record_generation(
        span,
        model="claude-3",
        input_tokens=200,
        output_tokens=80,
        latency_ms=99.9,
        cost_usd=0.002,
    )  # must not raise


async def test_record_event_no_io(provider: NoOpObsProvider, span: ObsSpan) -> None:
    await provider.record_event(span, "tool_call")  # must not raise


async def test_record_event_with_io(provider: NoOpObsProvider, span: ObsSpan) -> None:
    await provider.record_event(
        span,
        "tool_call",
        input_data={"arg": "value"},
        output_data={"result": 42},
    )  # must not raise


async def test_record_event_with_extra_kwargs(
    provider: NoOpObsProvider, span: ObsSpan
) -> None:
    await provider.record_event(span, "custom", level="debug")  # must not raise
