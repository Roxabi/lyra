"""Tests for OmpRpcDriver — RED phase.

These tests will fail at import until T10 implements:
  src/factory/llm/drivers/omp_rpc.py

Contract pinned here: OmpRpcDriver.__init__(nc, *, timeout_s=<default>),
is_alive() → True, complete() → LlmResult via NATS job envelope round-trip.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from factory.llm.drivers.omp_rpc import OmpRpcDriver  # ImportError until T10
from roxabi_contracts.jobs import JobResult
from roxabi_contracts.jobs.fixtures import sample_job_result_ok
from roxabi_contracts.jobs.subjects import jobs_result, jobs_submit

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def nc() -> AsyncMock:
    """Fake async NATS connection."""
    mock_nc = AsyncMock()
    sub = AsyncMock()
    mock_nc.subscribe.return_value = sub
    return mock_nc


@pytest.fixture()
def sub(nc: AsyncMock) -> AsyncMock:
    return nc.subscribe.return_value


@pytest.fixture()
def model_cfg() -> MagicMock:
    cfg = MagicMock()
    cfg.model_dump.return_value = {"backend": "omp-rpc", "model": "grok-4-fast"}
    return cfg


@pytest.fixture()
def driver(nc: AsyncMock) -> OmpRpcDriver:
    return OmpRpcDriver(nc, timeout_s=5.0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _encode_result(result: JobResult) -> bytes:
    return result.model_dump_json().encode()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestOmpRpcDriver:
    # -- capabilities ---

    def test_capabilities_declares_no_streaming(self, driver: OmpRpcDriver) -> None:
        """Class-level capabilities must declare streaming=False."""
        assert driver.capabilities == {"streaming": False}

    # -- is_alive ---

    def test_is_alive_always_true(self, driver: OmpRpcDriver) -> None:
        """V1: is_alive() always returns True regardless of pool_id."""
        assert driver.is_alive("any-pool") is True
        assert driver.is_alive("") is True

    # -- (a) happy path ---

    @pytest.mark.asyncio
    async def test_complete_happy_path(
        self,
        driver: OmpRpcDriver,
        nc: AsyncMock,
        sub: AsyncMock,
        model_cfg: MagicMock,
    ) -> None:
        """Success JobResult → LlmResult.ok, result extracted, envelope correct."""
        # Arrange
        success_result = JobResult.model_validate(
            {**sample_job_result_ok, "data": {"result": "ok"}}
        )
        sub.next_msg.return_value = SimpleNamespace(data=_encode_result(success_result))

        # Act
        res = await driver.complete(
            pool_id="p",
            text="hi",
            model_cfg=model_cfg,
            system_prompt="sys",
        )

        # Assert — result
        assert res.ok is True
        assert res.result == "ok"

        # Assert — publish subject
        publish_call = nc.publish.await_args
        assert publish_call is not None
        published_subject = publish_call.args[0]
        assert published_subject == jobs_submit("omp")
        assert published_subject == "factory.jobs.omp"

        # Assert — envelope payload
        published_bytes = publish_call.args[1]
        envelope = json.loads(published_bytes)
        payload = envelope["payload"]
        assert "prompt" in payload
        assert "model_cfg" in payload
        assert "system_prompt" in payload
        assert payload["prompt"] == "hi"

        # Assert — subscribe before publish (call order)
        # subscribe is an awaitable on AsyncMock; check relative ordering
        subscribe_idx = None
        publish_idx = None
        for i, call in enumerate(nc.mock_calls):
            name = call[0]
            if name == "subscribe" and subscribe_idx is None:
                subscribe_idx = i
            if name == "publish" and publish_idx is None:
                publish_idx = i
        assert subscribe_idx is not None, "nc.subscribe never called"
        assert publish_idx is not None, "nc.publish never called"
        assert subscribe_idx < publish_idx, (
            "nc.subscribe must be called before nc.publish"
        )

    # -- (a) result subject correctness --

    @pytest.mark.asyncio
    async def test_complete_subscribes_to_correct_result_subject(
        self,
        driver: OmpRpcDriver,
        nc: AsyncMock,
        sub: AsyncMock,
        model_cfg: MagicMock,
    ) -> None:
        """Driver subscribes to jobs_result(job_id) before publishing."""
        # Arrange
        success_result = JobResult.model_validate(
            {**sample_job_result_ok, "data": {"result": "r"}}
        )
        sub.next_msg.return_value = SimpleNamespace(data=_encode_result(success_result))

        # Act
        await driver.complete(
            pool_id="p", text="t", model_cfg=model_cfg, system_prompt="s"
        )

        # Assert — subscribe target matches jobs_result pattern
        subscribe_subject = nc.subscribe.call_args.args[0]
        # Must match factory.job.<uuid>.result
        assert subscribe_subject.startswith("factory.job.")
        assert subscribe_subject.endswith(".result")
        # jobs_result() applied to the job_id embedded in the subscribe call
        # We can verify format without knowing the exact uuid
        parts = subscribe_subject.split(".")
        # factory . job . <uuid> . result  → 4 parts (uuid4().hex is dotless)
        assert len(parts) == 4
        assert parts[0] == "factory"
        assert parts[1] == "job"
        assert parts[3] == "result"
        # Round-trip: jobs_result(extracted_id) == subscribe_subject
        extracted_id = parts[2]
        assert jobs_result(extracted_id) == subscribe_subject

    # -- (b) timeout --

    @pytest.mark.asyncio
    async def test_complete_timeout_returns_error(
        self,
        driver: OmpRpcDriver,
        sub: AsyncMock,
        model_cfg: MagicMock,
    ) -> None:
        """asyncio.TimeoutError → LlmResult.ok=False with non-empty error."""
        # Arrange
        sub.next_msg.side_effect = asyncio.TimeoutError

        # Act
        res = await driver.complete(
            pool_id="p",
            text="hi",
            model_cfg=model_cfg,
            system_prompt="sys",
        )

        assert res.ok is False
        assert res.error
        assert res.worker_error is not None
        assert res.worker_error.code == "transport.timeout"

    # -- (c) worker error --

    @pytest.mark.asyncio
    async def test_complete_worker_error_returns_error(
        self,
        driver: OmpRpcDriver,
        sub: AsyncMock,
        model_cfg: MagicMock,
    ) -> None:
        """Error-status JobResult → LlmResult.ok=False with non-empty error."""
        # Arrange — use the canonical error fixture shape from fixtures.py
        from roxabi_contracts.jobs.fixtures import sample_job_result_err

        error_result = JobResult.model_validate(sample_job_result_err)
        sub.next_msg.return_value = SimpleNamespace(data=_encode_result(error_result))

        # Act
        res = await driver.complete(
            pool_id="p",
            text="hi",
            model_cfg=model_cfg,
            system_prompt="sys",
        )

        assert res.ok is False
        assert res.error == "scraper failed"
        assert res.worker_error is not None
        assert res.worker_error.code == "worker.crash"

    # -- unsubscribe cleanup --

    @pytest.mark.asyncio
    async def test_complete_unsubscribes_after_success(
        self,
        driver: OmpRpcDriver,
        sub: AsyncMock,
        model_cfg: MagicMock,
    ) -> None:
        """sub.unsubscribe() must be called in finally — even on success."""
        # Arrange
        success_result = JobResult.model_validate(
            {**sample_job_result_ok, "data": {"result": "x"}}
        )
        sub.next_msg.return_value = SimpleNamespace(data=_encode_result(success_result))

        # Act
        await driver.complete(
            pool_id="p", text="t", model_cfg=model_cfg, system_prompt="s"
        )

        # Assert
        sub.unsubscribe.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_complete_unsubscribes_after_timeout(
        self,
        driver: OmpRpcDriver,
        sub: AsyncMock,
        model_cfg: MagicMock,
    ) -> None:
        """sub.unsubscribe() must be called in finally — even on timeout."""
        # Arrange
        sub.next_msg.side_effect = asyncio.TimeoutError

        # Act
        await driver.complete(
            pool_id="p", text="t", model_cfg=model_cfg, system_prompt="s"
        )

        # Assert
        sub.unsubscribe.assert_awaited_once()

    # -- (d) malformed result (B2 ValidationError branch) --

    @pytest.mark.asyncio
    async def test_complete_malformed_result_returns_non_retryable_error(
        self,
        driver: OmpRpcDriver,
        sub: AsyncMock,
        model_cfg: MagicMock,
    ) -> None:
        """Malformed reply → error LlmResult (retryable=False); unsubscribe still runs.

        Falsification: deleting the `except ValidationError` block lets an unhandled
        exception propagate instead of returning a clean LlmResult.
        Retryable: malformed=False vs status="error"=True (malformed is not transient).
        """
        # Arrange — raw bytes that are not valid JSON / JobResult schema
        sub.next_msg.return_value = SimpleNamespace(data=b"not-json-garbage")

        # Act
        res = await driver.complete(
            pool_id="p", text="hi", model_cfg=model_cfg, system_prompt="sys"
        )

        # Assert — error result, NOT retryable (malformed ≠ transient)
        assert res.ok is False
        assert res.retryable is False

        # Assert — cleanup runs even on malformed path (finally block)
        sub.unsubscribe.assert_awaited_once()
