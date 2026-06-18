"""OmpJobCodec — JobResult → LlmResult (ADR-089 S4)."""

from __future__ import annotations

from factory.llm.omp_job_codec import OmpJobCodec
from roxabi_contracts.errors import WorkerError
from roxabi_contracts.jobs import JobResult
from roxabi_contracts.jobs.fixtures import sample_job_result_err, sample_job_result_ok

_codec = OmpJobCodec()


class TestOmpJobCodecDecode:
    def test_success_extracts_result_text(self) -> None:
        result = JobResult.model_validate(
            {**sample_job_result_ok, "data": {"result": "pong"}}
        )
        llm = _codec.decode(result)
        assert llm.ok is True
        assert llm.result == "pong"
        assert llm.worker_error is None

    def test_error_populates_worker_error(self) -> None:
        result = JobResult.model_validate(sample_job_result_err)
        llm = _codec.decode(result)
        assert llm.ok is False
        assert llm.worker_error is not None
        assert llm.worker_error.code == "worker.crash"
        assert llm.error == "scraper failed"
        assert llm.retryable is True

    def test_decode_timeout_carries_transport_code(self) -> None:
        llm = _codec.decode_timeout()
        assert llm.ok is False
        assert llm.worker_error is not None
        assert llm.worker_error.code == "transport.timeout"

    def test_decode_malformed_is_non_retryable(self) -> None:
        llm = _codec.decode_malformed()
        assert llm.ok is False
        assert llm.retryable is False
        assert llm.worker_error is not None
        assert llm.worker_error.code == "transport.parse"
        assert llm.error == "omp returned a malformed result"

    def test_unregistered_error_code_maps_to_worker_internal(self) -> None:
        result = JobResult.model_validate(
            {
                **sample_job_result_err,
                "error": {
                    "code": "worker.bogus_future",
                    "message": "future error",
                    "retryable": True,
                },
            }
        )
        llm = _codec.decode(result)
        assert llm.worker_error is not None
        assert llm.worker_error.code == "worker.internal"
        assert llm.worker_error.message == "future error"
        assert llm.error == "future error"