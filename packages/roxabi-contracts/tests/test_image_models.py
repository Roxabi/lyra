"""Roundtrip + success-path invariant tests for roxabi_contracts.image models.

Parametrized over the three envelope subclasses (ImageRequest,
ImageResponse, ImageHeartbeat). Invariants per issue #806 (mirrors voice
drift items from spec #763).
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from roxabi_contracts.image import (
    ImageHeartbeat,
    ImageRequest,
    ImageResponse,
)
from roxabi_contracts.image.fixtures import (
    tiny_png_1x1,
    tiny_png_height,
    tiny_png_mime,
    tiny_png_width,
)

_ENVELOPE: dict[str, Any] = {
    "contract_version": "1",
    "trace_id": "tst-trace",
    "issued_at": datetime(2026, 4, 22, tzinfo=timezone.utc),
}


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _ok_response_payload() -> dict[str, Any]:
    return {
        **_ENVELOPE,
        "ok": True,
        "request_id": "r1",
        "image_b64": _b64(tiny_png_1x1),
        "mime_type": tiny_png_mime,
        "width": tiny_png_width,
        "height": tiny_png_height,
        "engine": "flux2-klein",
        "seed_used": 42,
    }


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (
            ImageRequest,
            {
                **_ENVELOPE,
                "request_id": "r1",
                "prompt": "a cat in space",
                "engine": "flux2-klein",
            },
        ),
        (ImageResponse, _ok_response_payload()),
        (
            ImageHeartbeat,
            {
                **_ENVELOPE,
                "worker_id": "img-1",
                "service": "image",
                "host": "h",
                "subject": "lyra.image.generate.request",
                "queue_group": "IMAGE_WORKERS",
                "ts": 1.0,
            },
        ),
    ],
    ids=["ImageRequest", "ImageResponse", "ImageHeartbeat"],
)
def test_roundtrip(model: type[BaseModel], payload: dict[str, Any]) -> None:
    """model.model_validate(payload) → dump_json → model_validate_json → equal."""
    inst = model.model_validate(payload)
    parsed = model.model_validate_json(inst.model_dump_json())
    assert parsed == inst


def test_image_response_success_accepts_complete_payload() -> None:
    """Valid ok=True payload with all required success fields parses cleanly."""
    resp = ImageResponse.model_validate(_ok_response_payload())
    assert resp.image_b64 is not None
    assert resp.mime_type == tiny_png_mime
    assert resp.width == tiny_png_width
    assert resp.height == tiny_png_height
    assert resp.engine == "flux2-klein"
    assert resp.seed_used == 42


def test_image_response_success_accepts_file_path_variant() -> None:
    """file_path is an accepted alternative to image_b64 when ok=True."""
    payload = _ok_response_payload()
    payload["image_b64"] = None
    payload["file_path"] = "/tmp/out.png"
    resp = ImageResponse.model_validate(payload)
    assert resp.file_path == "/tmp/out.png"
    assert resp.image_b64 is None


def test_image_response_ok_true_rejects_both_payload_fields() -> None:
    """Exactly-one invariant: image_b64 AND file_path set is a violation."""
    payload = _ok_response_payload()
    payload["file_path"] = "/tmp/out.png"
    with pytest.raises(ValidationError, match="exactly one"):
        ImageResponse.model_validate(payload)


def test_image_response_ok_true_rejects_neither_payload_field() -> None:
    """Exactly-one invariant: neither image_b64 nor file_path set is a violation."""
    payload = _ok_response_payload()
    payload["image_b64"] = None
    with pytest.raises(ValidationError, match="exactly one"):
        ImageResponse.model_validate(payload)


@pytest.mark.parametrize(
    "missing_field",
    ["mime_type", "width", "height", "engine", "seed_used"],
)
def test_image_response_ok_true_rejects_missing_metadata(missing_field: str) -> None:
    """ok=True with any missing metadata field raises ValidationError."""
    payload = _ok_response_payload()
    payload[missing_field] = None
    with pytest.raises(ValidationError, match="must carry"):
        ImageResponse.model_validate(payload)


def test_image_response_error_path_allows_null_success_fields() -> None:
    """Error path: success fields absent when ok=False."""
    resp = ImageResponse(
        **_ENVELOPE,
        ok=False,
        request_id="r1",
        error="engine_unavailable",
    )
    assert resp.image_b64 is None
    assert resp.file_path is None
    assert resp.mime_type is None
    assert resp.width is None
    assert resp.height is None


# ---------------------------------------------------------------------------
# Negative tests for `StringConstraints(min_length=1)` — pin the declared
# constraints so a silent weakening (e.g. dropping the Annotated wrapper)
# fails here.
# ---------------------------------------------------------------------------

_MIN_LENGTH_CASES: list[tuple[type[BaseModel], str]] = [
    (ImageRequest, "request_id"),
    (ImageRequest, "prompt"),
    (ImageRequest, "trace_id"),
    (ImageResponse, "request_id"),
    (ImageResponse, "trace_id"),
    (ImageHeartbeat, "worker_id"),
    (ImageHeartbeat, "trace_id"),
]


def _valid_payload_for(model: type[BaseModel]) -> dict[str, Any]:
    if model is ImageRequest:
        return {
            **_ENVELOPE,
            "request_id": "r1",
            "prompt": "hi",
            "engine": "flux2-klein",
        }
    if model is ImageResponse:
        return {**_ENVELOPE, "ok": False, "request_id": "r1", "error": "x"}
    if model is ImageHeartbeat:
        return {
            **_ENVELOPE,
            "worker_id": "img-1",
            "service": "image",
            "host": "h",
            "subject": "lyra.image.generate.request",
            "queue_group": "IMAGE_WORKERS",
            "ts": 1.0,
        }
    raise AssertionError(f"Unknown model {model!r}")


@pytest.mark.parametrize(
    ("model", "field"),
    _MIN_LENGTH_CASES,
    ids=[f"{m.__name__}.{f}" for m, f in _MIN_LENGTH_CASES],
)
def test_min_length_one_rejects_empty_string(
    model: type[BaseModel], field: str
) -> None:
    """Empty string for any ``min_length=1`` field raises ValidationError."""
    payload = _valid_payload_for(model)
    payload[field] = ""
    with pytest.raises(ValidationError):
        model.model_validate(payload)
