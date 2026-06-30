"""Hub-side client for voice lifecycle list/status (ADR-095)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from factory.nats.envelope_fields import mint_work_envelope_fields
from roxabi_contracts.voice import (
    SUBJECTS,
    VoiceLifecycleRequest,
    VoiceLifecycleResponse,
)

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS

log = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 5.0


class VoiceLifecycleClient:
    """Request-reply helper for voice worker catalogues."""

    def __init__(self, nc: NATS, *, timeout: float = _DEFAULT_TIMEOUT) -> None:
        self._nc = nc
        self._timeout = timeout

    async def list_tts(self, *, host: str | None = None) -> dict[str, Any] | None:
        return await self._request(
            SUBJECTS.tts_lifecycle_list,
            op="list",
            host=host,
        )

    async def list_stt(self, *, host: str | None = None) -> dict[str, Any] | None:
        return await self._request(
            SUBJECTS.stt_lifecycle_list,
            op="list",
            host=host,
        )

    async def capabilities(self) -> dict[str, Any]:
        tts = await self.list_tts()
        stt = await self.list_stt()
        return {"tts": tts, "stt": stt}

    async def _request(
        self,
        subject: str,
        *,
        op: str,
        host: str | None,
    ) -> dict[str, Any] | None:
        fields = mint_work_envelope_fields()
        req = VoiceLifecycleRequest(
            contract_version=fields.contract_version,
            trace_id=fields.trace_id,
            issued_at=fields.issued_at,
            request_id=str(uuid4()),
            host=host,
            op=op,  # type: ignore[arg-type]
        )
        try:
            msg = await self._nc.request(
                subject,
                req.model_dump_json(exclude_none=True).encode(),
                timeout=self._timeout,
            )
        except Exception as exc:  # noqa: BLE001 — DEBT:boundary-broad-catch# boundary: voice-lifecycle — NATS timeout/errors return None
            log.warning("voice lifecycle request failed subject=%s: %s", subject, exc)
            return None
        if not msg.data:
            return None
        try:
            resp = VoiceLifecycleResponse.model_validate_json(msg.data)
        except Exception:  # noqa: BLE001 — DEBT:boundary-broad-catch# boundary: voice-lifecycle — malformed reply returns None
            log.warning("voice lifecycle invalid response subject=%s", subject)
            return None
        if not resp.ok:
            log.warning(
                "voice lifecycle error subject=%s code=%s",
                subject,
                resp.worker_error.code if resp.worker_error else resp.error,
            )
            return None
        return resp.data or {}