"""Tests for roxabi_satellite.llm."""

from __future__ import annotations

import json

from roxabi_contracts.errors import WorkerError
from roxabi_satellite.llm.replies import build_llm_error_reply


def test_build_llm_error_reply_blocking() -> None:
    we = WorkerError(code="worker.internal", message="boom", retryable=False)
    raw = build_llm_error_reply(
        {"request_id": "req-1", "trace_id": "trace-1"},
        we,
        stream=False,
    )
    data = json.loads(raw)
    assert data["ok"] is False
    assert data["worker_error"]["code"] == "worker.internal"


def test_build_llm_error_reply_streaming() -> None:
    we = WorkerError(code="transport.timeout", message="slow", retryable=True)
    raw = build_llm_error_reply({"request_id": "req-2"}, we, stream=True)
    data = json.loads(raw)
    assert data["done"] is True
    assert data["is_error"] is True
    assert data["worker_error"]["retryable"] is True