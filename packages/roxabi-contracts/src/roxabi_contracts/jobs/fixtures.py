"""Test fixtures for roxabi_contracts.jobs. Pure dicts — no NATS imports."""

from datetime import datetime, timezone
from typing import Any

ENV_BASE: dict[str, Any] = {
    "contract_version": "1",
    "trace_id": "tst-jobs-trace",
    "issued_at": datetime(2026, 5, 4, tzinfo=timezone.utc),
}

sample_job_envelope: dict[str, Any] = {
    **ENV_BASE,
    "job_id": "job-uuid-1234",
    "job_name": "vault.add-from-url",
    "payload": {"url": "https://example.com"},
    "reply_to": "_INBOX.abc123",
}

sample_job_result_ok: dict[str, Any] = {
    **ENV_BASE,
    "job_id": "job-uuid-1234",
    "status": "success",
    "data": {"stored": True},
}

sample_job_result_err: dict[str, Any] = {
    **ENV_BASE,
    "job_id": "job-uuid-1234",
    "status": "error",
    "error": {"code": "worker.crash", "message": "scraper failed", "retryable": True},
}

sample_job_progress: dict[str, Any] = {
    **ENV_BASE,
    "job_id": "job-uuid-1234",
    "step": "fetching",
    "pct": 25.0,
}
