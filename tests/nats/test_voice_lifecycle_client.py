"""Tests for VoiceLifecycleClient."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from factory.core.trace import TraceContext
from factory.nats.voice.voice_lifecycle_client import VoiceLifecycleClient
from roxabi_contracts.envelope import CONTRACT_VERSION
from roxabi_contracts.voice import SUBJECTS, VoiceLifecycleResponse


class _Nats:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self.subject: str | None = None
        self.payload: bytes | None = None

    async def request(self, subject: str, payload: bytes, timeout: float = 5.0):
        self.subject = subject
        self.payload = payload
        return SimpleNamespace(data=self._data)


def test_list_tts_parses_ok_response() -> None:
    resp = VoiceLifecycleResponse(
        contract_version=CONTRACT_VERSION,
        trace_id=str(uuid4()),
        issued_at=datetime.now(timezone.utc),
        request_id="r1",
        ok=True,
        data={"engines": [{"name": "chatterbox", "supports_clone": True}]},
    )
    nc = _Nats(resp.model_dump_json().encode())
    data = asyncio.run(VoiceLifecycleClient(nc).list_tts())  # type: ignore[arg-type]
    assert nc.subject == SUBJECTS.tts_lifecycle_list
    assert data is not None
    assert data["engines"][0]["name"] == "chatterbox"


def test_list_stt_mints_trace_via_ssot_not_uuid4_remint() -> None:
    wire_trace = "550e8400-e29b-41d4-a716-446655440000"
    token = TraceContext.set_trace_id(wire_trace)
    try:
        resp = VoiceLifecycleResponse(
            contract_version=CONTRACT_VERSION,
            trace_id=str(uuid4()),
            issued_at=datetime.now(timezone.utc),
            request_id="r1",
            ok=True,
            data={"engines": []},
        )
        nc = _Nats(resp.model_dump_json().encode())
        asyncio.run(VoiceLifecycleClient(nc).list_stt())  # type: ignore[arg-type]
        assert nc.payload is not None
        req = json.loads(nc.payload.decode())
        assert req["trace_id"] == wire_trace
    finally:
        TraceContext.reset_trace_id(token)


def test_list_tts_generates_trace_when_context_unset() -> None:
    resp = VoiceLifecycleResponse(
        contract_version=CONTRACT_VERSION,
        trace_id=str(uuid4()),
        issued_at=datetime.now(timezone.utc),
        request_id="r1",
        ok=True,
        data={"engines": []},
    )
    nc = _Nats(resp.model_dump_json().encode())
    asyncio.run(VoiceLifecycleClient(nc).list_tts())  # type: ignore[arg-type]
    assert nc.payload is not None
    req = json.loads(nc.payload.decode())
    assert req["trace_id"]
    assert req["contract_version"] == CONTRACT_VERSION