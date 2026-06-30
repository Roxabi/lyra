"""OtelLifecycleHooks — MessageLifecycleHooks backed by OpenTelemetry SDK."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Span, Status, StatusCode

from roxabi_contracts.telemetry import (
    ATTR_COMPONENT,
    ATTR_ENVELOPE_NAME,
    ATTR_JOB_ID,
    ATTR_PARENT_JOB_ID,
    ATTR_POOL_ID,
    ATTR_SKILL,
    ATTR_SKILL_UNKNOWN,
    ATTR_SUBJECT,
    ATTR_TRACE_ID,
)

from .scrub import scrub_attrs


def otel_enabled() -> bool:
    flag = os.environ.get("ROXABI_OTEL_ENABLED", "1").strip().lower()
    return flag not in {"0", "false", "no", "off"}


@dataclass
class RecordedSpan:
    trace_id: str
    job_id: str
    name: str
    attributes: dict[str, Any]
    duration_ms: float | None = None


class InMemorySpanRecorder:
    """Test helper — captures exported spans without network."""

    def __init__(self) -> None:
        self._exporter = InMemorySpanExporter()
        self._provider = TracerProvider()
        self._provider.add_span_processor(SimpleSpanProcessor(self._exporter))
        self._tracer = self._provider.get_tracer("roxabi-otel-test")

    @property
    def exporter(self) -> InMemorySpanExporter:
        return self._exporter

    def hooks(self, service_name: str = "test-worker") -> OtelLifecycleHooks:
        return OtelLifecycleHooks(
            service_name=service_name,
            tracer_provider=self._provider,
        )

    def finished_spans(self) -> list[RecordedSpan]:
        out: list[RecordedSpan] = []
        for span in self._exporter.get_finished_spans():
            attrs = dict(span.attributes or {})
            wire_trace = str(attrs.get(ATTR_TRACE_ID, ""))
            out.append(
                RecordedSpan(
                    trace_id=wire_trace,
                    job_id=str(attrs.get(ATTR_JOB_ID, "")),
                    name=span.name,
                    attributes=attrs,
                    duration_ms=(
                        (span.end_time - span.start_time) / 1_000_000
                        if span.end_time and span.start_time
                        else None
                    ),
                )
            )
        return out

    def clear(self) -> None:
        self._exporter.clear()


class NoopHooks:
    """Default hooks — zero overhead when OTel disabled."""

    def on_work_start(self, **_kwargs: Any) -> None:
        return None

    def on_work_end(self, **_kwargs: Any) -> None:
        return None

    def record_domain_attrs(
        self,
        job_id: str,
        attrs: Mapping[str, str | int | float | bool],
    ) -> None:
        return None


@dataclass
class OtelLifecycleHooks:
    """Production/test OTel hooks implementation."""

    service_name: str
    tracer_provider: TracerProvider | None = None
    _spans: dict[str, Span] = field(default_factory=dict)
    _dropped_attrs: int = 0

    def __post_init__(self) -> None:
        if self.tracer_provider is None:
            resource = Resource.create({"service.name": self.service_name})
            self.tracer_provider = TracerProvider(resource=resource)
            endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
            if endpoint:
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                    OTLPSpanExporter,
                )

                self.tracer_provider.add_span_processor(
                    BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint))
                )
            trace.set_tracer_provider(self.tracer_provider)
        self._tracer = self.tracer_provider.get_tracer("roxabi.nats.worker")

    def on_work_start(  # noqa: PLR0913 — protocol signature
        self,
        *,
        trace_id: str,
        job_id: str,
        parent_job_id: str | None,
        pool_id: str | None,
        subject: str,
        envelope_name: str,
        queue_group: str,
    ) -> None:
        attrs: dict[str, str] = {
            ATTR_TRACE_ID: trace_id,
            ATTR_JOB_ID: job_id,
            ATTR_COMPONENT: queue_group,
            ATTR_ENVELOPE_NAME: envelope_name,
            ATTR_SUBJECT: subject,
            ATTR_SKILL: ATTR_SKILL_UNKNOWN,
        }
        if parent_job_id is not None:
            attrs[ATTR_PARENT_JOB_ID] = parent_job_id
        if pool_id is not None:
            attrs[ATTR_POOL_ID] = pool_id
        span = self._tracer.start_span(f"nats.work:{job_id}", attributes=attrs)
        self._spans[job_id] = span

    def on_work_end(
        self,
        *,
        trace_id: str,
        job_id: str,
        duration_ms: float,
        error: BaseException | None = None,
    ) -> None:
        span = self._spans.pop(job_id, None)
        if span is None:
            return
        span.set_attribute("roxabi.duration_ms", duration_ms)
        if error is not None:
            span.set_status(Status(StatusCode.ERROR, type(error).__name__))
        else:
            span.set_status(Status(StatusCode.OK))
        span.end()

    def record_domain_attrs(
        self,
        job_id: str,
        attrs: Mapping[str, str | int | float | bool],
    ) -> None:
        if not attrs:
            return
        cleaned, dropped = scrub_attrs(attrs)
        self._dropped_attrs += dropped
        if not cleaned:
            return
        span = self._spans.get(job_id)
        if span is None:
            return
        for key, value in cleaned.items():
            span.set_attribute(key, value)