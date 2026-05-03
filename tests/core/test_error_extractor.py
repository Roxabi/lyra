"""Unit tests for lyra.core.messaging.error_extractor._extract_worker_error."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pytest

from lyra.core.messaging.error_extractor import _extract_worker_error
from roxabi_contracts.errors import WorkerError

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


@dataclass
class _LegacyReply:
    """Simulates a legacy envelope without worker_error (e.g. CliControlAck)."""

    ok: bool
    pool_id: str = "pool-1"


@dataclass
class _ReplyWithError:
    """Envelope with worker_error populated and is_error=True (normal error path)."""

    is_error: bool
    worker_error: WorkerError | None


@dataclass
class _ReplyContradiction:
    """Envelope where is_error=False but worker_error is populated (contradiction)."""

    is_error: bool
    worker_error: WorkerError | None


@dataclass
class _ReplyOkContradiction:
    """Envelope where ok=True but worker_error populated (contradiction via ok)."""

    ok: bool
    worker_error: WorkerError | None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestExtractWorkerError:
    """_extract_worker_error covers all envelope shapes."""

    def test_legacy_reply_without_field_returns_none(self) -> None:
        """Legacy envelope with no worker_error attribute → None."""
        reply = _LegacyReply(ok=True)
        result = _extract_worker_error(reply)
        assert result is None

    def test_reply_with_none_worker_error_returns_none(self) -> None:
        """Envelope with worker_error=None explicitly → None."""
        reply = _ReplyWithError(is_error=False, worker_error=None)
        result = _extract_worker_error(reply)
        assert result is None

    def test_populated_worker_error_returned(self) -> None:
        """Envelope with worker_error populated → returns the same WorkerError."""
        we = WorkerError(code="cli.auth", message="Not logged in", retryable=False)
        reply = _ReplyWithError(is_error=True, worker_error=we)
        result = _extract_worker_error(reply)
        assert result is we

    def test_contradiction_is_error_false_returns_we_with_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """is_error=False + worker_error populated → returns WE + WARNING logged."""
        we = WorkerError(
            code="cli.session_lost", message="Session dropped", retryable=True
        )
        reply = _ReplyContradiction(is_error=False, worker_error=we)

        with caplog.at_level(
            logging.WARNING, logger="lyra.core.messaging.error_extractor"
        ):
            result = _extract_worker_error(reply)

        assert result is we
        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warning_records, "Expected at least one WARNING log record"
        assert "envelope contradiction" in warning_records[0].message

    def test_contradiction_ok_true_returns_we_with_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """ok=True AND worker_error populated → still returns WE + WARNING logged."""
        we = WorkerError(code="worker.crash", message="Worker died", retryable=True)
        reply = _ReplyOkContradiction(ok=True, worker_error=we)

        with caplog.at_level(
            logging.WARNING, logger="lyra.core.messaging.error_extractor"
        ):
            result = _extract_worker_error(reply)

        assert result is we
        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warning_records, "Expected at least one WARNING log record"
        assert "envelope contradiction" in warning_records[0].message
