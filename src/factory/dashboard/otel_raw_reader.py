"""Read otel-raw JSONL archive + SQLite index for dashboard BFF (#2069)."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _default_jsonl_path() -> Path:
    return Path(
        os.environ.get(
            "FACTORY_OTEL_JSONL_PATH",
            Path.home() / ".local/state/factory/otel/spans.jsonl",
        )
    )


def _default_db_path() -> Path:
    return Path(
        os.environ.get(
            "FACTORY_OTEL_RAW_DB",
            Path.home() / ".roxabi/factory/otel-raw.db",
        )
    )


_SCHEMA = """
CREATE TABLE IF NOT EXISTS spans (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trace_id TEXT NOT NULL,
  span_id TEXT NOT NULL,
  job_id TEXT,
  pool_id TEXT,
  component TEXT,
  envelope_name TEXT,
  subject TEXT,
  name TEXT NOT NULL,
  start_ts REAL NOT NULL,
  duration_ms REAL NOT NULL,
  attributes_json TEXT NOT NULL,
  ingested_at REAL NOT NULL,
  UNIQUE(trace_id, span_id)
);
CREATE INDEX IF NOT EXISTS idx_spans_trace ON spans(trace_id);
CREATE INDEX IF NOT EXISTS idx_spans_job ON spans(job_id);
CREATE INDEX IF NOT EXISTS idx_spans_pool ON spans(pool_id);
CREATE INDEX IF NOT EXISTS idx_spans_ts ON spans(start_ts);
CREATE INDEX IF NOT EXISTS idx_spans_component ON spans(component);
"""


@dataclass(frozen=True)
class SpanRow:
    trace_id: str
    span_id: str
    job_id: str | None
    pool_id: str | None
    component: str | None
    envelope_name: str | None
    subject: str | None
    name: str
    start_ts: float
    duration_ms: float
    attributes: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "job_id": self.job_id,
            "pool_id": self.pool_id,
            "component": self.component,
            "envelope_name": self.envelope_name,
            "subject": self.subject,
            "name": self.name,
            "start_ts": self.start_ts,
            "duration_ms": self.duration_ms,
            "attributes": self.attributes,
        }


class OtelRawReader:
    def __init__(
        self,
        *,
        jsonl_path: Path | None = None,
        db_path: Path | None = None,
    ) -> None:
        self._jsonl_path = jsonl_path or _default_jsonl_path()
        self._db_path = db_path or _default_db_path()
        self._indexed_mtime: float | None = None

    def _connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        conn.executescript(_SCHEMA)
        return conn

    def index_jsonl(self, *, force: bool = False) -> int:
        """Ingest new JSONL lines into SQLite; returns rows inserted."""
        if not self._jsonl_path.exists():
            return 0
        mtime = self._jsonl_path.stat().st_mtime
        if not force and self._indexed_mtime == mtime:
            return 0
        inserted = 0
        now = time.time()
        with self._connect() as conn:
            for line in self._jsonl_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = self._parse_line(line)
                except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                    continue
                if row is None:
                    continue
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO spans (
                      trace_id, span_id, job_id, pool_id, component,
                      envelope_name, subject, name, start_ts, duration_ms,
                      attributes_json, ingested_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row.trace_id,
                        row.span_id,
                        row.job_id,
                        row.pool_id,
                        row.component,
                        row.envelope_name,
                        row.subject,
                        row.name,
                        row.start_ts,
                        row.duration_ms,
                        json.dumps(row.attributes),
                        now,
                    ),
                )
                inserted += cur.rowcount
            conn.commit()
        self._indexed_mtime = mtime
        return inserted

    def _parse_line(self, line: str) -> SpanRow | None:
        data = json.loads(line)
        resource_spans = data.get("resourceSpans") or data.get("resource_spans")
        if resource_spans:
            return self._parse_otlp_batch(data)
        attrs = data.get("attributes") or {}
        if not isinstance(attrs, dict):
            attrs = {}
        trace_id = str(
            data.get("trace_id") or attrs.get("roxabi.trace_id") or ""
        )
        span_id = str(data.get("span_id") or attrs.get("span_id") or trace_id[:16])
        if not trace_id:
            return None
        start = float(data.get("start_time_unix_nano", 0)) / 1_000_000_000
        end = float(data.get("end_time_unix_nano", 0)) / 1_000_000_000
        duration_ms = max((end - start) * 1000.0, 0.0)
        return SpanRow(
            trace_id=trace_id,
            span_id=span_id,
            job_id=_str_or_none(attrs.get("roxabi.job_id")),
            pool_id=_str_or_none(attrs.get("roxabi.pool_id")),
            component=_str_or_none(attrs.get("roxabi.component")),
            envelope_name=_str_or_none(attrs.get("roxabi.envelope_name")),
            subject=_str_or_none(attrs.get("roxabi.subject")),
            name=str(data.get("name") or "span"),
            start_ts=start or time.time(),
            duration_ms=duration_ms,
            attributes=dict(attrs),
        )

    def _parse_otlp_batch(self, data: dict[str, Any]) -> SpanRow | None:
        for rs in data.get("resourceSpans") or []:
            for ss in rs.get("scopeSpans") or []:
                for span in ss.get("spans") or []:
                    attrs = _otlp_attrs(span.get("attributes") or [])
                    trace_id = _hex_id(span.get("traceId"))
                    span_id = _hex_id(span.get("spanId"))
                    if not trace_id or not span_id:
                        continue
                    start = int(span.get("startTimeUnixNano") or 0) / 1_000_000_000
                    end = int(span.get("endTimeUnixNano") or 0) / 1_000_000_000
                    return SpanRow(
                        trace_id=trace_id,
                        span_id=span_id,
                        job_id=_str_or_none(attrs.get("roxabi.job_id")),
                        pool_id=_str_or_none(attrs.get("roxabi.pool_id")),
                        component=_str_or_none(attrs.get("roxabi.component")),
                        envelope_name=_str_or_none(attrs.get("roxabi.envelope_name")),
                        subject=_str_or_none(attrs.get("roxabi.subject")),
                        name=str(span.get("name") or "span"),
                        start_ts=start or time.time(),
                        duration_ms=max((end - start) * 1000.0, 0.0),
                        attributes=attrs,
                    )
        return None

    def query_spans(
        self,
        *,
        pool_id: str | None = None,
        job_id: str | None = None,
        component: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[SpanRow], int]:
        self.index_jsonl()
        clauses: list[str] = []
        params: list[Any] = []
        if pool_id:
            clauses.append("pool_id = ?")
            params.append(pool_id)
        if job_id:
            clauses.append("job_id = ?")
            params.append(job_id)
        if component:
            clauses.append("component = ?")
            params.append(component)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        offset = max(page - 1, 0) * page_size
        with self._connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM spans {where}", params
            ).fetchone()[0]
            rows = conn.execute(
                f"""
                SELECT trace_id, span_id, job_id, pool_id, component,
                       envelope_name, subject, name, start_ts, duration_ms,
                       attributes_json
                FROM spans {where}
                ORDER BY start_ts DESC
                LIMIT ? OFFSET ?
                """,
                [*params, page_size, offset],
            ).fetchall()
        items = [
            SpanRow(
                trace_id=r[0],
                span_id=r[1],
                job_id=r[2],
                pool_id=r[3],
                component=r[4],
                envelope_name=r[5],
                subject=r[6],
                name=r[7],
                start_ts=r[8],
                duration_ms=r[9],
                attributes=json.loads(r[10]),
            )
            for r in rows
        ]
        return items, int(total)


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value)
    return s if s else None


def _hex_id(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.lower()
    return value.hex() if hasattr(value, "hex") else str(value)


def _otlp_attrs(raw: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in raw:
        key = item.get("key")
        if not isinstance(key, str):
            continue
        val = item.get("value") or {}
        if not isinstance(val, dict):
            continue
        if "stringValue" in val:
            out[key] = val["stringValue"]
        elif "intValue" in val:
            out[key] = int(val["intValue"])
        elif "doubleValue" in val:
            out[key] = float(val["doubleValue"])
        elif "boolValue" in val:
            out[key] = bool(val["boolValue"])
    return out