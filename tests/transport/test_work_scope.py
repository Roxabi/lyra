"""Unit tests for lyra.transport.work_scope.WorkScope (#1392)."""

from __future__ import annotations

import pytest

from lyra.transport.work_scope import WorkScope


def test_workscope_constructs_with_non_empty_trace_id() -> None:
    scope = WorkScope(
        platform="telegram",
        bot_id="bot-1",
        scope_id=42,
        trace_id="abc123",
    )
    assert scope.trace_id == "abc123"


def test_workscope_rejects_empty_trace_id() -> None:
    with pytest.raises(ValueError, match="trace_id must be non-empty"):
        WorkScope(
            platform="telegram",
            bot_id="bot-1",
            scope_id=42,
            trace_id="",
        )
