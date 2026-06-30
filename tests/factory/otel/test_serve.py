"""Tests for factory-otel HTTP + ingest service."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from google.protobuf.json_format import Parse
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)

from factory.otel.serve import build_app
from factory.otel.store import OtelRawStore


def _sample_export_request() -> ExportTraceServiceRequest:
    payload = {
        "resourceSpans": [
            {
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": "aa" * 16,
                                "spanId": "bb" * 8,
                                "name": "nats.work",
                                "startTimeUnixNano": 1_000_000_000,
                                "endTimeUnixNano": 2_000_000_000,
                                "attributes": [
                                    {
                                        "key": "roxabi.job_id",
                                        "value": {"stringValue": "a" * 32},
                                    },
                                    {
                                        "key": "roxabi.component",
                                        "value": {"stringValue": "clipool-workers"},
                                    },
                                ],
                            }
                        ]
                    }
                ]
            }
        ]
    }
    msg = ExportTraceServiceRequest()
    Parse(json.dumps(payload), msg)
    return msg


def test_otel_serve_ingest_and_query(tmp_path: Path) -> None:
    jsonl = tmp_path / "spans.jsonl"
    db = tmp_path / "otel-raw.db"
    store = OtelRawStore(jsonl_path=jsonl, db_path=db)
    app = build_app(token="test-token", store=store)
    client = TestClient(app)
    export = _sample_export_request()

    unauth = client.post(
        "/v1/traces",
        content=export.SerializeToString(),
        headers={"content-type": "application/x-protobuf"},
    )
    assert unauth.status_code == 401

    ingest = client.post(
        "/v1/traces",
        content=export.SerializeToString(),
        headers={
            "content-type": "application/x-protobuf",
            "Authorization": "Bearer test-token",
        },
    )
    assert ingest.status_code == 200

    query = client.get(
        "/api/spans",
        params={"job_id": "a" * 32, "component": "clipool-workers"},
        headers={"Authorization": "Bearer test-token"},
    )
    assert query.status_code == 200
    body = query.json()
    assert body["total"] == 1
    assert body["items"][0]["job_id"] == "a" * 32