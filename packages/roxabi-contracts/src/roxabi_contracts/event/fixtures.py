"""Test fixtures for roxabi_contracts.event.

Pure synthesized data. No runtime dependencies beyond the package itself.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

_ENV: dict[str, Any] = {
    "contract_version": "1",
    "trace_id": "evt-trace-001",
    "issued_at": datetime(2026, 5, 27, tzinfo=timezone.utc),
}


# --- LyraEvent fixtures ---


event_hub_startup: dict[str, Any] = {
    **_ENV,
    "service": "hub",
    "kind": "startup",
    "level": "info",
    "message": "Hub process started",
    "payload": {"pid": 1234, "version": "2.1.0"},
}
"""Synthetic hub startup event payload."""


event_hub_error: dict[str, Any] = {
    **_ENV,
    "service": "hub",
    "kind": "error",
    "level": "error",
    "message": "Unhandled exception in dispatcher",
    "payload": {"exception": "ValueError", "route": "telegram.inbound"},
}
"""Synthetic hub error event payload."""


event_telegram_lifecycle: dict[str, Any] = {
    **_ENV,
    "service": "telegram",
    "kind": "lifecycle.connected",
    "level": "info",
    "message": "Telegram adapter connected",
}
"""Synthetic telegram lifecycle event payload."""


# --- LyraMetric fixtures ---


metric_hub_request_count: dict[str, Any] = {
    **_ENV,
    "service": "hub",
    "name": "request.count",
    "metric_type": "counter",
    "value": 1.0,
    "unit": "count",
    "labels": {"platform": "telegram"},
}
"""Synthetic hub request counter metric payload."""


metric_hub_latency: dict[str, Any] = {
    **_ENV,
    "service": "hub",
    "name": "request.latency",
    "metric_type": "histogram",
    "value": 42.5,
    "unit": "ms",
    "labels": {"platform": "telegram", "route": "inbound"},
}
"""Synthetic hub latency histogram metric payload."""


metric_llm_queue_depth: dict[str, Any] = {
    **_ENV,
    "service": "llm",
    "name": "queue.depth",
    "metric_type": "gauge",
    "value": 3.0,
    "unit": "count",
}
"""Synthetic LLM queue depth gauge metric payload."""
