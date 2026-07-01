"""FastAPI app for factory-otel — OTLP HTTP ingest + span query API."""

from __future__ import annotations

import logging
import pathlib
import shutil
from typing import TYPE_CHECKING

from fastapi import FastAPI, Query, Request, Response
from google.protobuf.json_format import Parse
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)

from factory.otel.auth import BearerAuthMiddleware
from factory.otel.store import OtelRawStore

if TYPE_CHECKING:
    pass

_log = logging.getLogger(__name__)


def build_app(
    *,
    token_path: pathlib.Path | None = None,
    token: str | None = None,
    store: OtelRawStore | None = None,
) -> FastAPI:
    if token is None:
        if token_path is None:
            raise ValueError("Either token_path or token must be provided.")
        token = token_path.read_text().strip()

    otel_store = store or OtelRawStore()
    otel_store.ensure_dirs()

    app = FastAPI(title="factory-otel")
    app.state.store = otel_store
    app.add_middleware(BearerAuthMiddleware, token=token)

    @app.get("/healthz")
    async def healthz() -> dict:
        jsonl = otel_store.reader._jsonl_path  # noqa: SLF001
        used_pct: float | None = None
        try:
            usage = shutil.disk_usage(jsonl.parent)
            used_pct = round(usage.used / usage.total * 100, 1)
        except OSError:
            _log.warning("disk_usage failed for %s", jsonl.parent)
        return {
            "status": "ok",
            "disk_used_pct": used_pct,
            "jsonl_bytes": jsonl.stat().st_size if jsonl.exists() else 0,
        }

    @app.post("/v1/traces")
    async def ingest_traces(request: Request) -> Response:
        body = await request.body()
        if not body:
            return Response(status_code=400)
        content_type = request.headers.get("content-type", "")
        try:
            if "json" in content_type:
                msg = ExportTraceServiceRequest()
                Parse(body.decode("utf-8"), msg)
            else:
                msg = ExportTraceServiceRequest()
                msg.ParseFromString(body)
            otel_store.append_otlp(msg)
        except (OSError, ValueError) as exc:
            _log.warning("otel http export failed: %s", exc)
            return Response(status_code=500)
        return Response(status_code=200)

    @app.get("/api/spans")
    async def list_spans(
        pool_id: str | None = Query(default=None),
        job_id: str | None = Query(default=None),
        component: str | None = Query(default=None),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=50, ge=1, le=200),
    ) -> dict:
        items, total = otel_store.reader.safe_query_spans(
            pool_id=pool_id,
            job_id=job_id,
            component=component,
            page=page,
            page_size=page_size,
        )
        return {
            "items": [row.as_dict() for row in items],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    return app