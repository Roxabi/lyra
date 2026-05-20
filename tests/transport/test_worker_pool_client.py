"""T14/T15 tests — WorkerPoolClient CB + routing + stream_request.

Spec: artifacts/specs/1278-nats-transport-workerpool-spec.mdx (S3, B2).
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.transport._result import Err, InboxStream, Ok, SanitizedError
from lyra.transport.nats_request_response import NatsTransport
from lyra.transport.worker_pool_client import WorkerPoolClient


async def _async_gen(items):
    for item in items:
        yield item


def _make_pool_with_workers(*worker_ids: str):
    mock_transport = MagicMock(spec=NatsTransport)
    pool = WorkerPoolClient(
        mock_transport,
        hb_subject="hb",
        validate_worker_id=lambda _: None,
        name="test-pool",
    )
    for wid in worker_ids:
        pool._registry.record_heartbeat(
            {
                "worker_id": wid,
                "vram_used_mb": 0,
                "vram_total_mb": 0,
                "active_requests": 0,
            }
        )
    return pool, mock_transport


# ---------------------------------------------------------------------------
# T14 — CB + routing
# ---------------------------------------------------------------------------


class TestRequestWithRoutingCircuitOpen:
    @pytest.mark.asyncio
    async def test_request_with_routing_circuit_open_returns_err_without_transport_call(
        self,
    ):
        """CB open → Err(pool.circuit_open) without calling transport."""
        pool, mock_transport = _make_pool_with_workers("w-1")
        mock_transport.call = AsyncMock()

        # Force CB open: record_failure() threshold=3
        for _ in range(pool._cb.failure_threshold):
            pool._cb.record_failure()

        result = await pool.request_with_routing(lambda wid: f"subj.{wid}", b"payload")

        mock_transport.call.assert_not_called()
        assert isinstance(result, Err)
        assert result.error.code == "pool.circuit_open"
        assert result.error.retryable is True


class TestRequestWithRoutingIteratesWorkers:
    @pytest.mark.asyncio
    async def test_request_with_routing_iterates_scored_workers_subject_per_worker(
        self,
    ):
        """Workers seeded; subject_fn applied per worker; right subject passed."""
        pool, mock_transport = _make_pool_with_workers("w-1", "w-2")
        mock_transport.call = AsyncMock(return_value=Ok(b"response"))

        result = await pool.request_with_routing(lambda wid: f"subj.{wid}", b"payload")

        assert isinstance(result, Ok)
        mock_transport.call.assert_called_once()
        called_subject = mock_transport.call.call_args[0][0]
        assert called_subject.startswith("subj.")


class TestRequestWithRoutingMarksStaleOnTimeout:
    @pytest.mark.asyncio
    async def test_request_with_routing_marks_stale_on_timeout(self):
        """transport.call returns transport.timeout → mark_stale called for worker."""
        pool, mock_transport = _make_pool_with_workers("w-1")
        timeout_err = Err(
            SanitizedError(
                code="transport.timeout", message="TimeoutError", retryable=True
            )
        )
        mock_transport.call = AsyncMock(return_value=timeout_err)

        await pool.request_with_routing(lambda wid: f"subj.{wid}", b"payload")

        # w-1 must be stale: last_heartbeat set to 0.0
        assert pool._registry._workers["w-1"].last_heartbeat == 0.0


class TestRequestWithRoutingNoLiveWorkers:
    @pytest.mark.asyncio
    async def test_request_with_routing_no_live_workers_returns_pool_err(self):
        """Empty registry → Err(pool.no_live_workers) without transport.call."""
        pool, mock_transport = _make_pool_with_workers()
        mock_transport.call = AsyncMock()

        result = await pool.request_with_routing(lambda wid: f"subj.{wid}", b"payload")

        mock_transport.call.assert_not_called()
        assert isinstance(result, Err)
        assert result.error.code == "pool.no_live_workers"


class TestRequestWithRoutingMaxAttempts:
    @pytest.mark.asyncio
    async def test_request_with_routing_max_attempts_caps_iteration(self):
        """3 workers, max_attempts=2 → transport.call called exactly 2 times."""
        pool, mock_transport = _make_pool_with_workers("w-1", "w-2", "w-3")
        timeout_err = Err(
            SanitizedError(
                code="transport.timeout", message="TimeoutError", retryable=True
            )
        )
        mock_transport.call = AsyncMock(return_value=timeout_err)

        await pool.request_with_routing(
            lambda wid: f"subj.{wid}", b"payload", max_attempts=2
        )

        assert mock_transport.call.call_count == 2


class TestRequestWithRoutingStructuredLog:
    @pytest.mark.asyncio
    async def test_request_with_routing_emits_structured_log(self, caplog):
        """pool.routing log record contains worker_id, subject, attempt, result keys."""
        pool, mock_transport = _make_pool_with_workers("w-1")
        mock_transport.call = AsyncMock(return_value=Ok(b"ok"))

        with caplog.at_level(logging.INFO, logger="lyra.transport.worker_pool_client"):
            await pool.request_with_routing(lambda wid: f"subj.{wid}", b"payload")

        routing_records = [
            r for r in caplog.records if "pool.routing" in r.getMessage()
        ]
        assert len(routing_records) >= 1
        rec = routing_records[0]
        for key in ("worker_id", "subject", "attempt", "result"):
            assert hasattr(rec, key), f"Missing extra key: {key}"


# ---------------------------------------------------------------------------
# T15 — stream_request
# ---------------------------------------------------------------------------


class TestStreamRequestCircuitOpen:
    @pytest.mark.asyncio
    async def test_stream_request_circuit_open_yields_err_without_open_inbox(self):
        """CB open → single Err yielded; open_inbox never called."""
        pool, mock_transport = _make_pool_with_workers("w-1")
        mock_transport.open_inbox = MagicMock()

        for _ in range(pool._cb.failure_threshold):
            pool._cb.record_failure()

        results = [r async for r in pool.stream_request(b"payload")]

        mock_transport.open_inbox.assert_not_called()
        assert len(results) == 1
        assert isinstance(results[0], Err)
        assert results[0].error.code == "pool.circuit_open"


class TestStreamRequestComposesOpenInbox:
    @pytest.mark.asyncio
    async def test_stream_request_composes_open_inbox(self):
        """stream_request delegates to transport.open_inbox and yields its messages."""
        pool, mock_transport = _make_pool_with_workers("w-1")

        chunks = [Ok(b"chunk"), Ok(b"done")]
        inbox_stream = InboxStream(
            inbox_subject="_INBOX.x",
            messages=_async_gen(chunks),
        )

        async_cm = MagicMock()
        async_cm.__aenter__ = AsyncMock(return_value=inbox_stream)
        async_cm.__aexit__ = AsyncMock(return_value=False)
        mock_transport.open_inbox = MagicMock(return_value=async_cm)

        results = [r async for r in pool.stream_request(b"payload")]

        mock_transport.open_inbox.assert_called_once()
        assert results == [Ok(b"chunk"), Ok(b"done")]
