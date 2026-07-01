"""Hub OTel tracer — ingress + NATS client spans (#2069 Block 9)."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from factory.core.trace import TraceContext
from factory.nats.envelope_fields import mint_work_envelope_fields
from factory.obs import hub_tracer
from factory.obs.hub_tracer import hub_ingress_span, nats_client_span

_TRACE = "550e8400-e29b-41d4-a716-446655440000"


@pytest.fixture
def memory_exporter() -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    provider = hub_tracer.test_tracer_provider()
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    provider.add_span_processor(SimpleSpanProcessor(exporter))
    exporter.clear()
    return exporter


@pytest.fixture
def otel_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROXABI_OTEL_ENABLED", "1")


def test_hub_ingress_span_emitted(
    memory_exporter: InMemorySpanExporter, otel_on: None
) -> None:
    with hub_ingress_span(platform="telegram", bot_id="b1", msg_id="m1"):
        pass
    names = [s.name for s in memory_exporter.get_finished_spans()]
    assert "hub.ingress" in names


def test_nats_client_span_reads_envelope_ids(
    memory_exporter: InMemorySpanExporter, otel_on: None
) -> None:
    tok = TraceContext.set_trace_id(_TRACE)
    try:
        fields = mint_work_envelope_fields(job_id="b" * 32, pool_id="pool:1")
        payload = (
            '{"trace_id":"'
            + fields.trace_id
            + '","job_id":"'
            + fields.job_id
            + '"}'
        ).encode()
        with nats_client_span(name="stt", subject="factory.stt.x", payload=payload):
            pass
    finally:
        TraceContext.reset_trace_id(tok)
    span = memory_exporter.get_finished_spans()[-1]
    attrs = dict(span.attributes or {})
    assert attrs["roxabi.trace_id"] == _TRACE
    assert attrs["roxabi.job_id"] == "b" * 32
    assert attrs["roxabi.component"] == "stt"


def test_noop_when_otel_disabled(memory_exporter: InMemorySpanExporter) -> None:
    with patch.dict(os.environ, {"ROXABI_OTEL_ENABLED": "0"}):
        with hub_ingress_span(platform="telegram", bot_id="b1", msg_id="m1"):
            pass
    assert not memory_exporter.get_finished_spans()