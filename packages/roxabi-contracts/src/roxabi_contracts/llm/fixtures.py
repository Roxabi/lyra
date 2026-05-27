"""Test fixtures for roxabi_contracts.llm.

Pure dicts — no NATS imports, no model imports (avoids circular deps).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

ENV_BASE: dict[str, Any] = {
    "contract_version": "1",
    "trace_id": "tst-llm-trace",
    "issued_at": datetime(2026, 5, 27, tzinfo=timezone.utc),
}

sample_llm_heartbeat: dict[str, Any] = {
    **ENV_BASE,
    "worker_id": "llm-worker-1",
    "model_name": "mistral-7b",
    "status": "idle",
    "latency_ms": 42,
    "last_heartbeat_ts": 1716816000.0,
}
"""Canonical LLM heartbeat payload. Round-trips cleanly through ``LlmHeartbeat``.

Fields per issue #1038: model name, status, latency, last-heartbeat-ts.
"""
