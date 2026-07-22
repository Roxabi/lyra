"""Roundtrip + invariant tests for memory-domain contract models."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from roxabi_contracts.memory import (
    AssembleRequest,
    AssembleResponse,
    CaptureRequest,
    CaptureResponse,
    SearchHit,
    SearchRequest,
    SearchResponse,
    build_assemble_request,
    build_capture_request,
    build_search_request,
)

_ENV: dict[str, Any] = {
    "contract_version": "1",
    "trace_id": "tst-trace",
    "issued_at": datetime(2026, 7, 22, tzinfo=timezone.utc),
}


def test_capture_request_roundtrip() -> None:
    req = CaptureRequest(
        **_ENV,
        request_id="req-1",
        title="Cognee",
        body="AI memory platform",
        url="https://example.com",
        tags=["agents", "memory"],
    )
    restored = CaptureRequest.model_validate_json(req.model_dump_json())
    assert restored.title == "Cognee"
    assert restored.tags == ["agents", "memory"]
    assert restored.category == "references"
    assert restored.entry_type == "bookmark"


def test_capture_response_ok_requires_entry_id() -> None:
    with pytest.raises(ValidationError, match="entry_id"):
        CaptureResponse(**_ENV, ok=True, request_id="r1", entry_id=None)


def test_capture_response_ok_roundtrip() -> None:
    resp = CaptureResponse(**_ENV, ok=True, request_id="r1", entry_id=42)
    restored = CaptureResponse.model_validate_json(resp.model_dump_json())
    assert restored.entry_id == 42


def test_search_request_roundtrip() -> None:
    req = SearchRequest(**_ENV, request_id="s1", query="claude agents", limit=10)
    restored = SearchRequest.model_validate_json(req.model_dump_json())
    assert restored.query == "claude agents"
    assert restored.limit == 10


def test_search_response_empty_hits_ok() -> None:
    resp = SearchResponse(**_ENV, ok=True, request_id="s1", hits=[])
    assert resp.ok is True
    assert resp.hits == []


def test_search_hit_roundtrip() -> None:
    hit = SearchHit(
        entry_id=7,
        title="T",
        category="knowledge",
        entry_type="twitter",
        snippet="…",
    )
    restored = SearchHit.model_validate_json(hit.model_dump_json())
    assert restored.entry_id == 7


def test_assemble_request_defaults() -> None:
    req = AssembleRequest(**_ENV, request_id="a1")
    assert req.budget_tokens == 4000
    assert req.fresh_tail_days == 7
    assert req.goal is None


def test_assemble_response_empty_ok() -> None:
    resp = AssembleResponse(**_ENV, ok=True, request_id="a1", items=[], text="")
    assert resp.ok is True
    assert resp.tokens_used == 0


def test_builders_produce_valid_models() -> None:
    cap = build_capture_request(title="T", body="B", tags=["x"])
    assert cap.title == "T"
    assert cap.contract_version == "1"
    assert cap.job_id

    search = build_search_request(query="foo")
    assert search.query == "foo"

    assemble = build_assemble_request(goal="issue:42", budget_tokens=512)
    assert assemble.goal == "issue:42"
    assert assemble.budget_tokens == 512


def test_capture_rejects_unsafe_category() -> None:
    with pytest.raises(ValidationError):
        CaptureRequest(
            **_ENV,
            request_id="r1",
            title="T",
            body="B",
            category="--bad",
        )
