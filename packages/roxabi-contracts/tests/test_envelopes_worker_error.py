"""RED-phase tests asserting 5 reply envelopes accept worker_error: WorkerError | None.

Envelopes covered: CliChunkEvent, LlmChunkEvent, LlmResponse, TtsResponse,
SttResponse, ImageResponse.  CliControlAck is excluded per ADR-066.

All tests will fail with ImportError (roxabi_contracts.errors missing) until
T4 ships, and will subsequently fail on missing worker_error field until T7
ships.

Spec trace: SC-C2 / N5, N6 / S1
Plan task: T2
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import Any

import pytest

from roxabi_contracts import BlobRef
from roxabi_contracts.cli.models import CliChunkEvent
from roxabi_contracts.errors import WorkerError
from roxabi_contracts.image import ImageResponse
from roxabi_contracts.llm import LlmChunkEvent, LlmResponse
from roxabi_contracts.voice import SttResponse, TtsResponse
from roxabi_contracts.voice.fixtures import silence_wav_16khz

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_ENV: dict[str, Any] = {
    "contract_version": "1",
    "trace_id": "tst-trace-env",
    "issued_at": datetime(2026, 5, 2, tzinfo=timezone.utc),
}

_WORKER_ERROR = WorkerError(code="worker.crash", message="boom")


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


# ---------------------------------------------------------------------------
# CliChunkEvent
# ---------------------------------------------------------------------------


def test_cli_chunk_event_accepts_worker_error() -> None:
    """CliChunkEvent with worker_error constructs ok and dumps the field."""
    # Arrange / Act
    evt = CliChunkEvent(
        **_ENV,
        pool_id="pool-1",
        event_type="error",
        is_error=True,
        done=True,
        worker_error=_WORKER_ERROR,
    )

    # Assert
    dump = evt.model_dump()
    assert dump["worker_error"] is not None
    assert dump["worker_error"]["code"] == "worker.crash"
    assert dump["worker_error"]["message"] == "boom"


def test_cli_chunk_event_worker_error_defaults_none() -> None:
    """CliChunkEvent without worker_error: field is None by default."""
    # Arrange / Act
    evt = CliChunkEvent(**_ENV, pool_id="pool-1", event_type="text")

    # Assert
    assert evt.worker_error is None  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# LlmChunkEvent
# ---------------------------------------------------------------------------


def test_llm_chunk_event_accepts_worker_error() -> None:
    """LlmChunkEvent with worker_error constructs ok and dumps the field."""
    # Arrange / Act
    chunk = LlmChunkEvent(
        **_ENV,
        request_id="r1",
        done=True,
        is_error=True,
        worker_error=_WORKER_ERROR,
    )

    # Assert
    dump = chunk.model_dump()
    assert dump["worker_error"] is not None
    assert dump["worker_error"]["code"] == "worker.crash"


def test_llm_chunk_event_worker_error_defaults_none() -> None:
    """LlmChunkEvent without worker_error: field is None by default."""
    # Arrange / Act
    chunk = LlmChunkEvent(**_ENV, request_id="r1", delta="hello", done=False)

    # Assert
    assert chunk.worker_error is None  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# LlmResponse
# ---------------------------------------------------------------------------


def test_llm_response_accepts_worker_error() -> None:
    """LlmResponse (ok=False) with worker_error constructs ok and dumps the field."""
    # Arrange / Act
    resp = LlmResponse(
        **_ENV,
        ok=False,
        request_id="r1",
        error="model_unavailable",
        worker_error=_WORKER_ERROR,
    )

    # Assert
    dump = resp.model_dump()
    assert dump["worker_error"] is not None
    assert dump["worker_error"]["code"] == "worker.crash"


def test_llm_response_worker_error_defaults_none() -> None:
    """LlmResponse without worker_error: field is None by default."""
    # Arrange / Act
    resp = LlmResponse(**_ENV, ok=True, request_id="r1", text="all good")

    # Assert
    assert resp.worker_error is None  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# TtsResponse
# ---------------------------------------------------------------------------


def test_tts_response_accepts_worker_error() -> None:
    """TtsResponse (ok=False) with worker_error constructs ok and dumps the field."""
    # Arrange / Act
    resp = TtsResponse(
        **_ENV,
        ok=False,
        request_id="r1",
        error="engine_unavailable",
        worker_error=_WORKER_ERROR,
    )

    # Assert
    dump = resp.model_dump()
    assert dump["worker_error"] is not None
    assert dump["worker_error"]["code"] == "worker.crash"


def test_tts_response_worker_error_defaults_none() -> None:
    """TtsResponse without worker_error: field is None by default."""
    # Arrange / Act
    resp = TtsResponse(
        **_ENV,
        ok=True,
        request_id="r1",
        blob_ref=BlobRef(
            store_key="test-audio",
            content_hash="",
            mime="audio/wav",
            size=len(silence_wav_16khz),
            source="testing",
        ),
        mime_type="audio/wav",
        duration_ms=1000,
    )

    # Assert
    assert resp.worker_error is None  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# SttResponse
# ---------------------------------------------------------------------------


def test_stt_response_accepts_worker_error() -> None:
    """SttResponse (ok=False) with worker_error constructs ok and dumps the field."""
    # Arrange / Act
    resp = SttResponse(
        **_ENV,
        ok=False,
        request_id="r2",
        error="audio_decode_failed",
        worker_error=_WORKER_ERROR,
    )

    # Assert
    dump = resp.model_dump()
    assert dump["worker_error"] is not None
    assert dump["worker_error"]["code"] == "worker.crash"


def test_stt_response_worker_error_defaults_none() -> None:
    """SttResponse without worker_error: field is None by default."""
    # Arrange / Act
    resp = SttResponse(
        **_ENV,
        ok=True,
        request_id="r2",
        text="hello",
        language="en",
        duration_seconds=1.0,
    )

    # Assert
    assert resp.worker_error is None  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# ImageResponse
# ---------------------------------------------------------------------------


def test_image_response_accepts_worker_error() -> None:
    """ImageResponse (ok=False) with worker_error constructs ok and dumps the field."""
    # Arrange / Act
    resp = ImageResponse(
        **_ENV,
        ok=False,
        request_id="r1",
        error="engine_unavailable",
        worker_error=_WORKER_ERROR,
    )

    # Assert
    dump = resp.model_dump()
    assert dump["worker_error"] is not None
    assert dump["worker_error"]["code"] == "worker.crash"


def test_image_response_worker_error_defaults_none() -> None:
    """ImageResponse without worker_error: field is None by default."""
    # Arrange / Act
    resp = ImageResponse(**_ENV, ok=False, request_id="r1", error="x")

    # Assert
    assert resp.worker_error is None  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# ImageResponse.error_detail removal (ADR-066: dead field deleted in P1)
# ---------------------------------------------------------------------------


def test_image_response_error_detail_field_removed() -> None:
    """ImageResponse.model_fields must NOT contain 'error_detail' (dead field deleted)."""  # noqa: E501
    # Assert
    assert "error_detail" not in ImageResponse.model_fields, (
        "'error_detail' is a dead field that must be removed from ImageResponse "
        "per ADR-066 §'Adoption surface'"
    )


@pytest.mark.parametrize(
    ("envelope_cls", "kwargs"),
    [
        pytest.param(
            CliChunkEvent,
            {
                "pool_id": "pool-1",
                "event_type": "error",
                "is_error": True,
                "done": True,
            },
            id="CliChunkEvent",
        ),
        pytest.param(
            LlmChunkEvent,
            {"request_id": "r1", "done": True, "is_error": True},
            id="LlmChunkEvent",
        ),
        pytest.param(
            LlmResponse,
            {"ok": False, "request_id": "r1", "error": "fail"},
            id="LlmResponse",
        ),
        pytest.param(
            TtsResponse,
            {"ok": False, "request_id": "r1", "error": "fail"},
            id="TtsResponse",
        ),
        pytest.param(
            SttResponse,
            {"ok": False, "request_id": "r2", "error": "fail"},
            id="SttResponse",
        ),
        pytest.param(
            ImageResponse,
            {"ok": False, "request_id": "r1", "error": "fail"},
            id="ImageResponse",
        ),
    ],
)
def test_all_envelopes_have_worker_error_field(
    envelope_cls: type, kwargs: dict[str, Any]
) -> None:
    """worker_error field exists on model_fields for every covered envelope."""
    assert "worker_error" in envelope_cls.model_fields, (
        f"{envelope_cls.__name__}.model_fields is missing 'worker_error' "
        f"(additive field per ADR-066)"
    )


@pytest.mark.parametrize(
    ("envelope_cls", "kwargs"),
    [
        pytest.param(
            CliChunkEvent,
            {
                "pool_id": "pool-1",
                "event_type": "error",
                "is_error": True,
                "done": True,
            },
            id="CliChunkEvent",
        ),
        pytest.param(
            LlmChunkEvent,
            {"request_id": "r1", "done": True, "is_error": True},
            id="LlmChunkEvent",
        ),
        pytest.param(
            LlmResponse,
            {"ok": False, "request_id": "r1", "error": "fail"},
            id="LlmResponse",
        ),
        pytest.param(
            TtsResponse,
            {"ok": False, "request_id": "r1", "error": "fail"},
            id="TtsResponse",
        ),
        pytest.param(
            SttResponse,
            {"ok": False, "request_id": "r2", "error": "fail"},
            id="SttResponse",
        ),
        pytest.param(
            ImageResponse,
            {"ok": False, "request_id": "r1", "error": "fail"},
            id="ImageResponse",
        ),
    ],
)
def test_all_envelopes_worker_error_roundtrip(
    envelope_cls: type, kwargs: dict[str, Any]
) -> None:
    """worker_error survives JSON round-trip on every covered envelope."""
    # Arrange
    inst = envelope_cls(**_ENV, **kwargs, worker_error=_WORKER_ERROR)

    # Act
    restored = envelope_cls.model_validate_json(inst.model_dump_json())

    # Assert
    assert restored.worker_error is not None
    assert restored.worker_error.code == "worker.crash"
    assert restored.worker_error.message == "boom"
