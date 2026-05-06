"""Test fixtures for roxabi_contracts.gh. Pure dicts — no NATS imports."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

ENV_BASE: dict[str, Any] = {
    "contract_version": "1",
    "trace_id": "tst-gh-trace",
    "issued_at": datetime(2026, 5, 4, tzinfo=timezone.utc),
}

sample_mint_failure: dict[str, Any] = {
    **ENV_BASE,
    "machine": "roxabituwer",
    "reason": "github_api_401",
    "http_status": 401,
    "retries": 2,
}
