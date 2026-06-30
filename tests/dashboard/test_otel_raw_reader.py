"""Tests for otel-raw SQLite reader/indexer."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from factory.dashboard.otel_raw_reader import OtelRawReader


def test_index_and_query_spans(tmp_path: Path) -> None:
    jsonl = tmp_path / "spans.jsonl"
    db = tmp_path / "otel-raw.db"
    line = {
        "trace_id": "abc",
        "span_id": "def",
        "name": "nats.work",
        "start_time_unix_nano": 1_000_000_000,
        "end_time_unix_nano": 2_000_000_000,
        "attributes": {
            "roxabi.job_id": "a" * 32,
            "roxabi.pool_id": "pool-1",
            "roxabi.component": "clipool-workers",
        },
    }
    jsonl.write_text(json.dumps(line) + "\n", encoding="utf-8")
    reader = OtelRawReader(jsonl_path=jsonl, db_path=db)
    inserted = reader.index_jsonl(force=True)
    assert inserted == 1
    items, total = reader.query_spans(job_id="a" * 32, component="clipool-workers")
    assert total == 1
    assert items[0].pool_id == "pool-1"


def test_index_otlp_batch_with_multiple_spans(tmp_path) -> None:
    jsonl = tmp_path / "spans.jsonl"
    db = tmp_path / "otel-raw.db"
    job_a = "a" * 32
    job_b = "b" * 32
    batch = {
        "resourceSpans": [
            {
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": "aa" * 16,
                                "spanId": "11" * 8,
                                "name": "nats.work:a",
                                "startTimeUnixNano": 1_000_000_000,
                                "endTimeUnixNano": 2_000_000_000,
                                "attributes": [
                                    {
                                        "key": "roxabi.job_id",
                                        "value": {"stringValue": job_a},
                                    }
                                ],
                            },
                            {
                                "traceId": "bb" * 16,
                                "spanId": "22" * 8,
                                "name": "nats.work:b",
                                "startTimeUnixNano": 3_000_000_000,
                                "endTimeUnixNano": 4_000_000_000,
                                "attributes": [
                                    {
                                        "key": "roxabi.job_id",
                                        "value": {"stringValue": job_b},
                                    }
                                ],
                            },
                        ]
                    }
                ]
            }
        ]
    }
    jsonl.write_text(json.dumps(batch) + "\n", encoding="utf-8")
    reader = OtelRawReader(jsonl_path=jsonl, db_path=db)
    inserted = reader.index_jsonl(force=True)
    assert inserted == 2
    items, total = reader.query_spans()
    assert total == 2
    job_ids = {row.job_id for row in items}
    assert job_ids == {job_a, job_b}


def test_safe_query_spans_degrades_on_readonly_db(tmp_path: Path) -> None:
    jsonl = tmp_path / "otel-data" / "spans.jsonl"
    jsonl.parent.mkdir()
    line = {
        "trace_id": "ro-trace",
        "span_id": "ro-span",
        "name": "nats.work",
        "start_time_unix_nano": 1_000_000_000,
        "end_time_unix_nano": 2_000_000_000,
        "attributes": {"roxabi.job_id": "d" * 32, "roxabi.component": "omp-workers"},
    }
    jsonl.write_text(json.dumps(line) + "\n", encoding="utf-8")
    index_dir = tmp_path / "otel-index"
    index_dir.mkdir()
    db = index_dir / "otel-raw.db"
    db.touch()
    os.chmod(db, stat.S_IRUSR | stat.S_IRGRP)
    os.chmod(index_dir, stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP)
    reader = OtelRawReader(jsonl_path=jsonl, db_path=db)
    try:
        items, total = reader.safe_query_spans(job_id="d" * 32)
    finally:
        os.chmod(index_dir, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP)
        os.chmod(db, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP)
    assert items == []
    assert total == 0