"""FakeTtsWorker + FakeSttWorker — test doubles for roxabi_contracts.voice.

Moved from roxabi_contracts.voice.testing per ADR-059 V6 (natural ownership
in the NATS transport package). Three non-bypassable guards prevent production
contamination. See spec #764 and ADR-049 §Test-double pattern.

Guard 1 (import-time): nats-py is imported at module top; installing
    roxabi-nats WITHOUT the [testing] extra fails with
    ModuleNotFoundError at import.
Guard 2 (env): __init__ raises RuntimeError when LYRA_ENV == "production".
Guard 3 (loopback): start() raises ValueError on non-loopback NATS URL.
"""

from __future__ import annotations

# Guard 1 tripwire — LOAD-BEARING. Do NOT move below other imports, and
# do NOT wrap in try/except. This import is the first runtime event when
# `roxabi_nats.testing.voice` is loaded; without the [testing] extra,
# `nats-py` is absent and the import fails with ModuleNotFoundError before
# any class definition is reached.
import nats  # noqa: F401  # pyright: ignore[reportUnusedImport]  # isort:skip

import asyncio
import base64
import logging
from datetime import datetime, timezone

from nats.aio.client import Client as NATS
from nats.aio.msg import Msg
from nats.aio.subscription import Subscription
from pydantic import ValidationError

from roxabi_contracts.voice.fixtures import sample_transcript_en, silence_wav_16khz
from roxabi_contracts.voice.models import (
    SttRequest,
    SttResponse,
    TtsRequest,
    TtsResponse,
)
from roxabi_contracts.voice.subjects import SUBJECTS
from roxabi_nats.connect import nats_connect
from roxabi_nats.testing._guards import assert_loopback_url, assert_not_production

__all__: list[str] = ["FakeTtsWorker", "FakeSttWorker"]

log = logging.getLogger(__name__)

_DRAIN_TIMEOUT_S: float = 2.0


class FakeTtsWorker:
    def __init__(
        self,
        nats_url: str = "nats://127.0.0.1:4222",
        reply_fixture: bytes | None = None,
    ) -> None:
        assert_not_production("FakeTtsWorker")
        self._nats_url = nats_url
        self._reply_fixture: bytes = (
            reply_fixture if reply_fixture is not None else silence_wav_16khz
        )
        self._nc: NATS | None = None
        self._sub: Subscription | None = None
        self.calls: list[TtsRequest] = []

    async def start(self) -> None:
        assert_loopback_url(self._nats_url)
        if self._nc is not None:
            raise RuntimeError("FakeTtsWorker already started")
        # `allow_reconnect=False, connect_timeout=2` bound the nats-py handshake;
        # the outer `asyncio.wait_for(..., timeout=3.0)` catches kernel-level
        # stalls (e.g., kernel TCP socket stuck in SYN_SENT). Both layers are
        # load-bearing for the Guard 3 loopback-accept tests where no nats-server
        # is running on the loopback port — without them the tests would hang
        # until the library's default 30s reconnect window elapses.
        self._nc = await asyncio.wait_for(
            nats_connect(self._nats_url, allow_reconnect=False, connect_timeout=2),
            timeout=3.0,
        )
        try:
            self._sub = await self._nc.subscribe(
                SUBJECTS.tts_request, queue=SUBJECTS.tts_workers, cb=self._dispatch
            )
        except Exception:
            await self._nc.close()
            self._nc = None
            raise

    async def stop(self) -> None:
        if self._nc is not None and self._nc.is_connected:
            try:
                await asyncio.wait_for(self._nc.drain(), timeout=_DRAIN_TIMEOUT_S)
            except asyncio.TimeoutError:  # pragma: no cover
                # TODO(#761 follow-up): add explicit drain-timeout test when the first
                # domain fake (TTS or image) hits a real slow-drain scenario in CI.
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
        try:
            await self._nc.publish(msg.reply, reply.model_dump_json().encode())
        except Exception:
            log.debug("FakeTtsWorker skipping reply — connection closing")


class FakeSttWorker:
    def __init__(
        self,
        nats_url: str = "nats://127.0.0.1:4222",
        reply_fixture: str | None = None,
    ) -> None:
        assert_not_production("FakeSttWorker")
        self._nats_url = nats_url
        self._reply_fixture: str = (
            reply_fixture if reply_fixture is not None else sample_transcript_en
        )
        self._nc: NATS | None = None
        self._sub: Subscription | None = None
        self.calls: list[SttRequest] = []

    async def start(self) -> None:
        assert_loopback_url(self._nats_url)
        if self._nc is not None:
            raise RuntimeError("FakeSttWorker already started")
        # `allow_reconnect=False, connect_timeout=2` bound the nats-py handshake;
        # the outer `asyncio.wait_for(..., timeout=3.0)` catches kernel-level
        # stalls (e.g., kernel TCP socket stuck in SYN_SENT). Both layers are
        # load-bearing for the Guard 3 loopback-accept tests where no nats-server
        # is running on the loopback port — without them the tests would hang
        # until the library's default 30s reconnect window elapses.
        self._nc = await asyncio.wait_for(
            nats_connect(self._nats_url, allow_reconnect=False, connect_timeout=2),
            timeout=3.0,
        )
        try:
            self._sub = await self._nc.subscribe(
                SUBJECTS.stt_request, queue=SUBJECTS.stt_workers, cb=self._dispatch
            )
        except Exception:
            await self._nc.close()
            self._nc = None
            raise

    async def stop(self) -> None:
        if self._nc is not None and self._nc.is_connected:
            try:
                await asyncio.wait_for(self._nc.drain(), timeout=_DRAIN_TIMEOUT_S)
            except asyncio.TimeoutError:  # pragma: no cover
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
        try:
            await self._nc.publish(msg.reply, reply.model_dump_json().encode())
        except Exception:
            log.debug("FakeSttWorker skipping reply — connection closing")
