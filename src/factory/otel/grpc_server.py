"""OTLP gRPC trace receiver for factory-otel."""

from __future__ import annotations

import logging
from concurrent import futures
from typing import TYPE_CHECKING

import grpc
from opentelemetry.proto.collector.trace.v1 import (
    trace_service_pb2,
    trace_service_pb2_grpc,
)

if TYPE_CHECKING:
    from factory.otel.store import OtelRawStore

log = logging.getLogger(__name__)


class _TraceService(trace_service_pb2_grpc.TraceServiceServicer):
    def __init__(self, store: OtelRawStore) -> None:
        self._store = store

    def Export(
        self,
        request: trace_service_pb2.ExportTraceServiceRequest,
        context: grpc.ServicerContext,
    ) -> trace_service_pb2.ExportTraceServiceResponse:
        try:
            self._store.append_otlp(request)
        except OSError as exc:
            log.warning("otel grpc export failed: %s", exc)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(exc))
            return trace_service_pb2.ExportTraceServiceResponse()
        return trace_service_pb2.ExportTraceServiceResponse()


def start_grpc_server(store: OtelRawStore, *, port: int = 4317) -> grpc.Server:
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    trace_service_pb2_grpc.add_TraceServiceServicer_to_server(
        _TraceService(store), server
    )
    server.add_insecure_port(f"[::]:{port}")
    server.start()
    log.info("factory-otel OTLP gRPC listening on %s", port)
    return server