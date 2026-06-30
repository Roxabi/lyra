"""Hub-side OTel spans — ingress turns and NATS client calls (#2069 Block 9)."""

from __future__ import annotations

import os
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, Status, StatusCode
from roxabi_otel import otel_enabled

from factory.nats.envelope_fields import peek_envelope_ids
from roxabi_contracts.telemetry import (
    ATTR_COMPONENT,
    ATTR_JOB_ID,
    ATTR_POOL_ID,
    ATTR_SUBJECT,
    ATTR_TRACE_ID,
)

_provider: TracerProvider | None = None


def _hub_tracer() -> trace.Tracer:
    global _provider
    if _provider is None:
        resource = Resource.create({"service.name": "factory-hub"})
        _provider = TracerProvider(resource=resource)
        endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
        if endpoint and otel_enabled():
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )

            _provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint)))
        trace.set_tracer_provider(_provider)
    return _provider.get_tracer("factory.hub")


def _span_attrs(  # noqa: PLR0913
    *,
    subject: str,
    component: str,
    trace_id: str | None,
    job_id: str | None,
    pool_id: str | None,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    attrs: dict[str, str] = {
        ATTR_COMPONENT: component,
        ATTR_SUBJECT: subject,
    }
    if trace_id:
        attrs[ATTR_TRACE_ID] = trace_id
    if job_id:
        attrs[ATTR_JOB_ID] = job_id
    if pool_id:
        attrs[ATTR_POOL_ID] = pool_id
    if extra:
        attrs.update(extra)
    return attrs


def _finish_span(span: Span, *, error: BaseException | None, started: float) -> None:
    duration_ms = round((time.monotonic() - started) * 1000, 2)
    span.set_attribute("roxabi.duration_ms", duration_ms)
    if error is not None:
        span.set_status(Status(StatusCode.ERROR, type(error).__name__))
    else:
        span.set_status(Status(StatusCode.OK))
    span.end()


@contextmanager
def hub_ingress_span(
    *,
    platform: str,
    bot_id: str,
    msg_id: str,
) -> Iterator[Span | None]:
    """Span for one inbound hub turn (TraceMiddleware)."""
    if not otel_enabled():
        yield None
        return
    tracer = _hub_tracer()
    span = tracer.start_span(
        "hub.ingress",
        attributes={
            ATTR_COMPONENT: "hub",
            "roxabi.platform": platform,
            "roxabi.bot_id": bot_id,
            "roxabi.msg_id": msg_id,
        },
    )
    started = time.monotonic()
    err: BaseException | None = None
    try:
        yield span
    except BaseException as exc:
        err = exc
        raise
    finally:
        _finish_span(span, error=err, started=started)


@contextmanager
def nats_client_span(  # noqa: PLR0913
    *,
    name: str,
    subject: str,
    payload: bytes | None = None,
    trace_id: str | None = None,
    job_id: str | None = None,
    pool_id: str | None = None,
) -> Iterator[Span | None]:
    """Client span for hub → worker NATS request (WorkerPoolClient, job drivers)."""
    if not otel_enabled():
        yield None
        return
    if payload is not None and (trace_id is None or job_id is None):
        peek_trace, peek_job = peek_envelope_ids(payload)
        trace_id = trace_id or peek_trace
        job_id = job_id or peek_job
    tracer = _hub_tracer()
    span = tracer.start_span(
        f"nats.client:{name}",
        attributes=_span_attrs(
            subject=subject,
            component=name,
            trace_id=trace_id,
            job_id=job_id,
            pool_id=pool_id,
        ),
    )
    started = time.monotonic()
    err: BaseException | None = None
    try:
        yield span
    except BaseException as exc:
        err = exc
        raise
    finally:
        _finish_span(span, error=err, started=started)


@asynccontextmanager
async def nats_client_stream_span(
    *,
    name: str,
    subject: str,
    payload: bytes | None = None,
) -> AsyncIterator[Span | None]:
    """Client span wrapping a streaming NATS inbox request."""
    if not otel_enabled():
        yield None
        return
    trace_id, job_id = (None, None)
    if payload is not None:
        trace_id, job_id = peek_envelope_ids(payload)
    tracer = _hub_tracer()
    span = tracer.start_span(
        f"nats.client.stream:{name}",
        attributes=_span_attrs(
            subject=subject,
            component=name,
            trace_id=trace_id,
            job_id=job_id,
            pool_id=None,
        ),
    )
    started = time.monotonic()
    err: BaseException | None = None
    try:
        yield span
    except BaseException as exc:
        err = exc
        raise
    finally:
        _finish_span(span, error=err, started=started)


def test_tracer_provider() -> TracerProvider:
    """Test-only provider for hub span assertions."""
    global _provider
    resource = Resource.create({"service.name": "factory-hub-test"})
    _provider = TracerProvider(resource=resource)
    trace.set_tracer_provider(_provider)
    return _provider