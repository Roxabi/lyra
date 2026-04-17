"""Forward-compat invariant for roxabi_contracts.voice models.

ADR-049 §Versioning: a consumer of schema v0.1.0 receiving a v0.2.0
payload with new optional fields must parse cleanly. Unknown fields
are silently dropped (ConfigDict(extra="ignore") inherited from
ContractEnvelope).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest
from pydantic import BaseModel

from roxabi_contracts.voice import (
    SttRequest,
    SttResponse,
    TtsRequest,
    TtsResponse,
)

_ENVELOPE: dict[str, Any] = {
    "contract_version": "1",
    "trace_id": "tst-trace",
    "issued_at": datetime(2026, 4, 17, tzinfo=timezone.utc).isoformat(),
}

_REQUIRED: list[tuple[type[BaseModel], dict[str, Any]]] = [
    (TtsRequest, {"request_id": "r1", "text": "hello"}),
    (TtsResponse, {"ok": False, "request_id": "r1", "error": "engine_unavailable"}),
    (SttRequest, {"request_id": "r2", "audio_b64": "AAAA", "model": "m"}),
    (SttResponse, {"ok": False, "request_id": "r2", "error": "audio_decode_failed"}),
]


@pytest.mark.parametrize(
    ("model", "required"),
    _REQUIRED,
    ids=["TtsRequest", "TtsResponse", "SttRequest", "SttResponse"],
)
def test_extra_field_dropped_dict_path(
    model: type[BaseModel], required: dict[str, Any]
) -> None:
    inst = model.model_validate(
        {**_ENVELOPE, **required, "surprise_field": "x", "nested": {"a": 1}}
    )
    dumped = inst.model_dump()
    assert "surprise_field" not in dumped
    assert "nested" not in dumped


@pytest.mark.parametrize(
    ("model", "required"),
    _REQUIRED,
    ids=["TtsRequest", "TtsResponse", "SttRequest", "SttResponse"],
)
def test_extra_field_dropped_json_path(
    model: type[BaseModel], required: dict[str, Any]
) -> None:
    payload = json.dumps({**_ENVELOPE, **required, "future_v2_field": 42})
    inst = model.model_validate_json(payload)
    assert "future_v2_field" not in inst.model_dump()
