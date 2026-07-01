"""OMP contract gate — golden wire shapes + LlmResult invariants.

Run the full OMP contract suite:

    pytest -m omp_contract
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from factory.core.envelope_fields import WorkEnvelopeFields
from factory.core.trace import TraceContext
from factory.llm.drivers.omp_rpc import OmpRpcDriver
from factory.llm.omp_job_codec import OmpJobCodec
from roxabi_contracts.jobs import JobResult
from roxabi_contracts.jobs.fixtures import sample_job_result_err, sample_job_result_ok
from roxabi_contracts.jobs.subjects import jobs_result, jobs_submit
from tests.contracts.omp_invariants import (
    assert_llm_result_invariant,
    llm_result_shape,
)

pytestmark = pytest.mark.omp_contract

_FIXED_JOB_ID = "deadbeefcafebabe0123456789abcdef"
_FIXED_ISSUED_AT = datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)

_GOLDEN_JOB_ENVELOPE: dict[str, object] = {
    "contract_version": "1",
    "trace_id": _FIXED_JOB_ID,
    "issued_at": "2026-06-18T12:00:00Z",
    "job_id": _FIXED_JOB_ID,
    "job_name": "omp",
    "payload": {
        "prompt": "ping",
        "model_cfg": {"backend": "omp-rpc", "model": "grok-4-fast"},
        "system_prompt": "sys",
        "pool_id": "pool-golden",
    },
    "reply_to": f"_INBOX.{_FIXED_JOB_ID}",
    "composite_depth": 0,
    "parent_job_id": None,
}

_GOLDEN_LLM_OK: dict[str, object] = {
    "result": "pong",
    "error": "",
    "retryable": True,
    "worker_error": None,
}

_GOLDEN_LLM_WORKER_CRASH: dict[str, object] = {
    "result": "",
    "error": "scraper failed",
    "retryable": True,
    "worker_error": {
        "code": "worker.crash",
        "message": "scraper failed",
        "retryable": True,
    },
}

_GOLDEN_LLM_TIMEOUT: dict[str, object] = {
    "result": "",
    "error": "omp request timed out",
    "retryable": True,
    "worker_error": {
        "code": "transport.timeout",
        "message": "omp request timed out",
        "retryable": True,
    },
}

_GOLDEN_LLM_MALFORMED: dict[str, object] = {
    "result": "",
    "error": "omp returned a malformed result",
    "retryable": False,
    "worker_error": {
        "code": "transport.parse",
        "message": "omp returned a malformed result",
        "retryable": False,
    },
}


def _model_cfg() -> MagicMock:
    cfg = MagicMock()
    cfg.model_dump.return_value = {"backend": "omp-rpc", "model": "grok-4-fast"}
    return cfg


def _encode_result(result: JobResult) -> bytes:
    return result.model_dump_json().encode()


class TestOmpRpcDriverGoldenEnvelope:
    @pytest.mark.asyncio
    async def test_complete_publishes_golden_job_envelope(self) -> None:
        """Pinned JobEnvelope JSON from OmpRpcDriver.complete() (frozen ids)."""
        nc = AsyncMock()
        sub = AsyncMock()
        nc.subscribe.return_value = sub
        success = JobResult.model_validate(
            {
                **sample_job_result_ok,
                "job_id": _FIXED_JOB_ID,
                "data": {"result": "pong"},
            }
        )
        sub.next_msg.return_value = SimpleNamespace(data=_encode_result(success))
        driver = OmpRpcDriver(nc, timeout_s=5.0)

        fixed_fields = WorkEnvelopeFields(
            contract_version="1",
            trace_id=_FIXED_JOB_ID,
            issued_at=_FIXED_ISSUED_AT,
            job_id=_FIXED_JOB_ID,
            parent_job_id=None,
            pool_id="pool-golden",
        )

        with patch(
            "factory.llm.drivers.omp_rpc.mint_work_envelope_fields",
            return_value=fixed_fields,
        ):
            await driver.complete(
                pool_id="pool-golden",
                text="ping",
                model_cfg=_model_cfg(),
                system_prompt="sys",
            )

        publish_call = nc.publish.await_args
        assert publish_call is not None
        assert publish_call.args[0] == jobs_submit("omp")
        assert publish_call.args[0] == "factory.jobs.omp"

        envelope = json.loads(publish_call.args[1])
        assert envelope == _GOLDEN_JOB_ENVELOPE

        subscribe_subject = nc.subscribe.call_args.args[0]
        assert subscribe_subject == jobs_result(_FIXED_JOB_ID)


class TestOmpCodecGoldenLlmResult:
    @pytest.mark.parametrize(
        ("fixture", "golden"),
        [
            pytest.param(
                {**sample_job_result_ok, "data": {"result": "pong"}},
                _GOLDEN_LLM_OK,
                id="success",
            ),
            pytest.param(
                sample_job_result_err,
                _GOLDEN_LLM_WORKER_CRASH,
                id="worker.crash",
            ),
        ],
    )
    def test_decode_matches_golden_shape(
        self,
        fixture: dict[str, object],
        golden: dict[str, object],
    ) -> None:
        codec = OmpJobCodec()
        result = JobResult.model_validate(fixture)
        llm = codec.decode(result)
        assert_llm_result_invariant(llm)
        assert llm_result_shape(llm) == golden

    def test_decode_timeout_matches_golden_shape(self) -> None:
        llm = OmpJobCodec().decode_timeout()
        assert_llm_result_invariant(llm)
        assert llm_result_shape(llm) == _GOLDEN_LLM_TIMEOUT

    def test_decode_malformed_matches_golden_shape(self) -> None:
        llm = OmpJobCodec().decode_malformed()
        assert_llm_result_invariant(llm)
        assert llm_result_shape(llm) == _GOLDEN_LLM_MALFORMED


class TestLlmResultShapeInvariant:
    @pytest.mark.parametrize(
        "fixture",
        [
            pytest.param(
                {**sample_job_result_ok, "data": {"result": "x"}},
                id="success",
            ),
            pytest.param(sample_job_result_err, id="worker.crash"),
            pytest.param(
                {
                    **sample_job_result_err,
                    "error": {
                        "code": "llm.rate_limit",
                        "message": "quota",
                        "retryable": True,
                    },
                },
                id="llm.rate_limit",
            ),
        ],
    )
    def test_codec_decode_satisfies_invariant(self, fixture: dict[str, object]) -> None:
        llm = OmpJobCodec().decode(JobResult.model_validate(fixture))
        assert_llm_result_invariant(llm)

    @pytest.mark.asyncio
    async def test_driver_paths_satisfy_invariant(self) -> None:
        TraceContext.set_trace_id(_FIXED_JOB_ID)
        nc = AsyncMock()
        sub = AsyncMock()
        nc.subscribe.return_value = sub
        driver = OmpRpcDriver(nc, timeout_s=5.0)
        model_cfg = _model_cfg()

        sub.next_msg.return_value = SimpleNamespace(
            data=_encode_result(
                JobResult.model_validate(
                    {**sample_job_result_ok, "data": {"result": "ok"}}
                )
            )
        )
        assert_llm_result_invariant(await driver.complete("p", "t", model_cfg, "s"))

        sub.next_msg.return_value = SimpleNamespace(
            data=_encode_result(JobResult.model_validate(sample_job_result_err))
        )
        assert_llm_result_invariant(await driver.complete("p", "t", model_cfg, "s"))

        sub.next_msg.side_effect = asyncio.TimeoutError
        assert_llm_result_invariant(await driver.complete("p", "t", model_cfg, "s"))

        sub.next_msg.side_effect = None
        sub.next_msg.return_value = SimpleNamespace(data=b"not-json")
        assert_llm_result_invariant(await driver.complete("p", "t", model_cfg, "s"))
