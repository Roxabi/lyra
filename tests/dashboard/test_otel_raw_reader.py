"""Tests for otel-raw SQLite reader/indexer."""

from __future__ import annotations

import json
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