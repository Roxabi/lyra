"""Tests for TtsCodec.decode — structured error fields + unavailable flag."""

from __future__ import annotations

import json

from lyra.nats.nats_tts_codec import TtsCodec
from lyra.transport._result import Err, Ok, SanitizedError
from roxabi_contracts import BlobRef
from roxabi_contracts.envelope import CONTRACT_VERSION

_CODEC = TtsCodec()

_FAKE_BLOB = BlobRef(
    store_key="test", content_hash="deadbeef", mime="audio/ogg", size=4, source="test"
)


def _ok_response_bytes(
    request_id: str = "req-1",
    blob_ref: BlobRef = _FAKE_BLOB,
) -> bytes:
    """Build a minimal valid TtsResponse bytes for the success path."""
    payload = {
        "contract_version": CONTRACT_VERSION,
        "trace_id": "trace-1",
        "issued_at": "2024-01-01T00:00:00Z",
        "ok": True,
        "request_id": request_id,
        "blob_ref": {
            "store_key": blob_ref.store_key,
            "content_hash": blob_ref.content_hash,
            "mime": blob_ref.mime,
            "size": blob_ref.size,
            "source": blob_ref.source,
        },
        "mime_type": "audio/ogg",
        "duration_ms": 500,
    }
    return json.dumps(payload).encode("utf-8")


def _error_response_bytes(
    *,
    error: str | None = None,
    worker_error: dict | None = None,
    request_id: str = "req-err",
) -> bytes:
    """Build a TtsResponse bytes for an error path."""
    payload: dict = {
        "contract_version": CONTRACT_VERSION,
        "trace_id": "trace-err",
        "issued_at": "2024-01-01T00:00:00Z",
        "ok": False,
        "request_id": request_id,
    }
    if error is not None:
        payload["error"] = error
    if worker_error is not None:
        payload["worker_error"] = worker_error
    return json.dumps(payload).encode("utf-8")


class TestTtsCodecDecodeSuccess:
    def test_success_path_returns_audio_fields(self) -> None:
        result = _CODEC.decode(Ok(_ok_response_bytes()))
        assert result.error == ""
        assert result.unavailable is False
        assert result.mime_type == "audio/ogg"
        assert result.duration_ms == 500
        assert result.blob_ref is not None
        assert result.blob_ref.store_key == _FAKE_BLOB.store_key

    def test_success_path_no_error_message(self) -> None:
        result = _CODEC.decode(Ok(_ok_response_bytes()))
        assert result.error_message == ""
        assert result.error_detail is None


class TestTtsCodecDecodeTransportErr:
    def test_transport_err_sets_unavailable_true(self) -> None:
        err = Err(
            SanitizedError(
                code="pool.no_live_workers",
                message="NoLiveWorkers",
                retryable=True,
            )
        )
        result = _CODEC.decode(err)
        assert result.error == "pool.no_live_workers"
        assert result.error_message == "NoLiveWorkers"
        assert result.retryable is True
        assert result.unavailable is True

    def test_transport_timeout_sets_unavailable_true(self) -> None:
        err = Err(
            SanitizedError(
                code="transport.timeout",
                message="TimeoutError",
                retryable=True,
            )
        )
        result = _CODEC.decode(err)
        assert result.unavailable is True
        assert result.error == "transport.timeout"

    def test_transport_err_never_raises(self) -> None:
        err = Err(
            SanitizedError(code="pool.circuit_open", message="open", retryable=True)
        )
        # Must not raise
        result = _CODEC.decode(err)
        assert result.error == "pool.circuit_open"


class TestTtsCodecDecodeValidationError:
    def test_invalid_json_returns_validation_error(self) -> None:
        result = _CODEC.decode(Ok(b"not-valid-json"))
        assert result.error == "decode.validation_error"
        assert result.unavailable is False
        assert result.retryable is False

    def test_validation_error_never_raises(self) -> None:
        result = _CODEC.decode(Ok(b"{malformed"))
        assert result.error == "decode.validation_error"


class TestTtsCodecDecodeWorkerError:
    def test_structured_worker_error_preferred_over_flat(self) -> None:
        payload = _error_response_bytes(
            error="voice.engine_unavailable",
            worker_error={
                "code": "voice.invalid_voice",
                "message": "Voice Cherry not found",
                "retryable": False,
                "detail": "Available: alloy, echo",
            },
        )
        result = _CODEC.decode(Ok(payload))
        assert result.error == "voice.invalid_voice"
        assert result.error_message == "Voice Cherry not found"
        assert result.error_detail == "Available: alloy, echo"
        assert result.retryable is False
        assert result.unavailable is False

    def test_structured_worker_error_without_detail(self) -> None:
        payload = _error_response_bytes(
            worker_error={
                "code": "worker.validation",
                "message": "Invalid text length",
                "retryable": False,
            },
        )
        result = _CODEC.decode(Ok(payload))
        assert result.error == "worker.validation"
        assert result.error_detail is None
        assert result.unavailable is False

    def test_flat_error_fallback_when_worker_error_absent(self) -> None:
        payload = _error_response_bytes(error="voice.engine_unavailable")
        result = _CODEC.decode(Ok(payload))
        assert result.error == "voice.engine_unavailable"
        assert result.error_message == "voice.engine_unavailable"
        assert result.retryable is False
        assert result.unavailable is False

    def test_flat_error_none_uses_default_code(self) -> None:
        payload = _error_response_bytes()  # no error, no worker_error
        result = _CODEC.decode(Ok(payload))
        assert result.error == "tts.worker_error"
        assert result.unavailable is False

    def test_worker_error_retryable_true_propagated(self) -> None:
        payload = _error_response_bytes(
            worker_error={
                "code": "worker.capacity",
                "message": "Queue full",
                "retryable": True,
            },
        )
        result = _CODEC.decode(Ok(payload))
        assert result.retryable is True
        assert result.unavailable is False
