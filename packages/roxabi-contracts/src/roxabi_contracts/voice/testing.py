"""FakeTtsWorker + FakeSttWorker — test doubles for roxabi_contracts.voice.

Three non-bypassable guards prevent production contamination. See spec #764
and ADR-049 §Test-double pattern.

Guard 1 (import-time): nats-py is imported at module top; installing
    roxabi-contracts WITHOUT the [testing] extra fails with
    ModuleNotFoundError at import.
Guard 2 (env): __init__ raises RuntimeError when LYRA_ENV == "production".
Guard 3 (loopback): start() raises ValueError on non-loopback NATS URL.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
from datetime import datetime, timezone
from urllib.parse import urlparse

from nats.aio.client import Client as NATS
from nats.aio.msg import Msg
from nats.aio.subscription import Subscription
from pydantic import ValidationError

# Guard 1: fails at import with ModuleNotFoundError when [testing] extra
# is not installed. Do NOT wrap in try/except — that would defeat Guard 1.
import nats  # noqa: F401  # pyright: ignore[reportUnusedImport]
from roxabi_contracts.voice.fixtures import sample_transcript_en, silence_wav_16khz
from roxabi_contracts.voice.models import (
    SttRequest,
    SttResponse,
    TtsRequest,
    TtsResponse,
)
from roxabi_contracts.voice.subjects import SUBJECTS
from roxabi_nats.connect import nats_connect

__all__: list[str] = ["FakeTtsWorker", "FakeSttWorker"]

log = logging.getLogger(__name__)

_DRAIN_TIMEOUT_S: float = 2.0

ALLOWED_LOOPBACK_HOSTS: frozenset[str] = frozenset(
    {"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"}
)


def _assert_not_production(cls_name: str) -> None:
    """Guard 2 — raises RuntimeError when LYRA_ENV=production (case-insensitive)."""
    if os.environ.get("LYRA_ENV", "").casefold() == "production":
        raise RuntimeError(f"{cls_name} cannot run in production")


def _assert_loopback_url(url: str) -> None:
    """Guard 3 — raises ValueError when the URL hostname is not loopback."""
    host = urlparse(url).hostname
    if host not in ALLOWED_LOOPBACK_HOSTS:
        raise ValueError(
            f"loopback NATS URL required — refusing host {host!r}; "
            f"allowed: {sorted(ALLOWED_LOOPBACK_HOSTS)}"
        )


class FakeTtsWorker:
    def __init__(
        self,
        nats_url: str = "nats://127.0.0.1:4222",
        reply_fixture: bytes | None = None,
    ) -> None:
        _assert_not_production("FakeTtsWorker")
        self._nats_url = nats_url
        self._reply_fixture: bytes = (
            reply_fixture if reply_fixture is not None else silence_wav_16khz
        )
        self._nc: NATS | None = None
        self._sub: Subscription | None = None
        self.calls: list[TtsRequest] = []

    async def start(self) -> None:
        _assert_loopback_url(self._nats_url)
        if self._nc is not None:
            raise RuntimeError("FakeTtsWorker already started")
        self._nc = await asyncio.wait_for(
            nats_connect(self._nats_url, allow_reconnect=False, connect_timeout=2),
            timeout=3.0,
        )
        self._sub = await self._nc.subscribe(
            SUBJECTS.tts_request, queue=SUBJECTS.tts_workers, cb=self._dispatch
        )

    async def stop(self) -> None:
        if self._nc is not None and self._nc.is_connected:
            try:
                await asyncio.wait_for(self._nc.drain(), timeout=_DRAIN_TIMEOUT_S)
            except asyncio.TimeoutError:
                log.warning(
                    "FakeTtsWorker drain timed out after %.1fs", _DRAIN_TIMEOUT_S
                )
        self._sub = None
        self._nc = None

    async def _dispatch(self, msg: Msg) -> None:
        try:
            req = TtsRequest.model_validate_json(msg.data)
        except ValidationError as exc:
            log.warning("FakeTtsWorker dropped malformed request: %s", exc)
            return
        self.calls.append(req)
        if not msg.reply or self._nc is None:
            return
        reply = TtsResponse(
            contract_version=req.contract_version,
            trace_id=req.trace_id,
            issued_at=datetime.now(timezone.utc),
            ok=True,
            request_id=req.request_id,
            audio_b64=base64.b64encode(self._reply_fixture).decode("ascii"),
            mime_type="audio/wav",
            duration_ms=1000,
        )
        await self._nc.publish(msg.reply, reply.model_dump_json().encode())


class FakeSttWorker:
    def __init__(
        self,
        nats_url: str = "nats://127.0.0.1:4222",
        reply_fixture: str | None = None,
    ) -> None:
        _assert_not_production("FakeSttWorker")
        self._nats_url = nats_url
        self._reply_fixture: str = (
            reply_fixture if reply_fixture is not None else sample_transcript_en
        )
        self._nc: NATS | None = None
        self._sub: Subscription | None = None
        self.calls: list[SttRequest] = []

    async def start(self) -> None:
        _assert_loopback_url(self._nats_url)
        if self._nc is not None:
            raise RuntimeError("FakeSttWorker already started")
        self._nc = await asyncio.wait_for(
            nats_connect(self._nats_url, allow_reconnect=False, connect_timeout=2),
            timeout=3.0,
        )
        self._sub = await self._nc.subscribe(
            SUBJECTS.stt_request, queue=SUBJECTS.stt_workers, cb=self._dispatch
        )

    async def stop(self) -> None:
        if self._nc is not None and self._nc.is_connected:
            try:
                await asyncio.wait_for(self._nc.drain(), timeout=_DRAIN_TIMEOUT_S)
            except asyncio.TimeoutError:
                log.warning(
                    "FakeSttWorker drain timed out after %.1fs", _DRAIN_TIMEOUT_S
                )
        self._sub = None
        self._nc = None

    async def _dispatch(self, msg: Msg) -> None:
        try:
            req = SttRequest.model_validate_json(msg.data)
        except ValidationError as exc:
            log.warning("FakeSttWorker dropped malformed request: %s", exc)
            return
        self.calls.append(req)
        if not msg.reply or self._nc is None:
            return
        reply = SttResponse(
            contract_version=req.contract_version,
            trace_id=req.trace_id,
            issued_at=datetime.now(timezone.utc),
            ok=True,
            request_id=req.request_id,
            text=self._reply_fixture,
            language="en",
            duration_seconds=1.0,
        )
        await self._nc.publish(msg.reply, reply.model_dump_json().encode())
