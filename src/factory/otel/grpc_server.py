"""OTLP gRPC trace receiver for factory-otel."""

from __future__ import annotations

import hmac
import logging
from collections.abc import Callable
from concurrent import futures
from typing import TYPE_CHECKING, Any

import grpc
from opentelemetry.proto.collector.trace.v1 import (
    trace_service_pb2,
    trace_service_pb2_grpc,
)

if TYPE_CHECKING:
    from factory.otel.store import OtelRawStore

log = logging.getLogger(__name__)


class _BearerAuthInterceptor(grpc.ServerInterceptor):
    def __init__(self, token: str) -> None:
        self._token = token

    def intercept_service(
        self,
        continuation: Callable[..., Any],
        handler_call_details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler | None:
        metadata = {
            key.lower(): (
                value.decode("utf-8") if isinstance(value, bytes) else str(value)
            )
            for key, value in handler_call_details.invocation_metadata
        }
        auth = metadata.get("authorization", "")
        if not auth.startswith("Bearer "):
            return _unauthenticated_handler(handler_call_details)
        provided = auth.removeprefix("Bearer ")
        if not hmac.compare_digest(provided.encode(), self._token.encode()):
            return _unauthenticated_handler(handler_call_details)
        return continuation(handler_call_details)


def _unauthenticated_handler(
    handler_call_details: grpc.HandlerCallDetails,
) -> grpc.RpcMethodHandler:
    def _reject(
        request: Any,
        context: grpc.ServicerContext,
    ) -> trace_service_pb2.ExportTraceServiceResponse:
        del request
        context.abort(grpc.StatusCode.UNAUTHENTICATED, "unauthorized")
        return trace_service_pb2.ExportTraceServiceResponse()

    if handler_call_details.method.endswith("/Export"):
        return grpc.unary_unary_rpc_method_handler(
            _reject,
            request_deserializer=trace_service_pb2.ExportTraceServiceRequest.FromString,
            response_serializer=trace_service_pb2.ExportTraceServiceResponse.SerializeToString,
        )
    return grpc.unary_unary_rpc_method_handler(_reject)


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


def start_grpc_server(
    store: OtelRawStore,
    *,
    port: int = 4317,
    token: str | None = None,
) -> grpc.Server:
    interceptors: list[grpc.ServerInterceptor] = []
    if token:
        interceptors.append(_BearerAuthInterceptor(token))
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=4),
        interceptors=interceptors,
    )
    trace_service_pb2_grpc.add_TraceServiceServicer_to_server(
        _TraceService(store), server
    )
    server.add_insecure_port(f"[::]:{port}")
    server.start()
    log.info("factory-otel OTLP gRPC listening on %s", port)
    return server