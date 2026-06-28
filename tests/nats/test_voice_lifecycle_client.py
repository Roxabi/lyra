"""Tests for VoiceLifecycleClient."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import asyncio

from roxabi_contracts.envelope import CONTRACT_VERSION
from roxabi_contracts.voice import SUBJECTS, VoiceLifecycleResponse
from factory.nats.voice.voice_lifecycle_client import VoiceLifecycleClient


class _Nats:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self.subject: str | None = None

    async def request(self, subject: str, payload: bytes, timeout: float = 5.0):
        self.subject = subject
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