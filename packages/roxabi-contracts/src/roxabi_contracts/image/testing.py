"""FakeImageWorker — test double for roxabi_contracts.image.

Three non-bypassable guards prevent production contamination. Mirrors the
voice testing-double pattern (spec #764, ADR-049 §Test-double pattern).

Guard 1 (import-time): nats-py is imported at module top; installing
    roxabi-contracts WITHOUT the [testing] extra fails with
    ModuleNotFoundError at import.
Guard 2 (env): __init__ raises RuntimeError when LYRA_ENV == "production".
Guard 3 (loopback): start() raises ValueError on non-loopback NATS URL.
"""

from __future__ import annotations

# Guard 1 tripwire — LOAD-BEARING. Do NOT move below other imports, and
# do NOT wrap in try/except. This import is the first runtime event when
# `roxabi_contracts.image.testing` is loaded; without the [testing] extra,
# `nats-py` is absent and the import fails with ModuleNotFoundError before
# any class definition is reached.
import nats  # noqa: F401  # pyright: ignore[reportUnusedImport]  # isort:skip

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

from roxabi_contracts.image.fixtures import (
    tiny_png_1x1,
    tiny_png_height,
    tiny_png_mime,
    tiny_png_width,
)
from roxabi_contracts.image.models import ImageRequest, ImageResponse
from roxabi_contracts.image.subjects import SUBJECTS
from roxabi_nats.connect import nats_connect

__all__: list[str] = ["FakeImageWorker"]

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


class FakeImageWorker:
    def __init__(
        self,
        nats_url: str = "nats://127.0.0.1:4222",
        reply_fixture: bytes | None = None,
    ) -> None:
        _assert_not_production("FakeImageWorker")
        self._nats_url = nats_url
        self._reply_fixture: bytes = (
            reply_fixture if reply_fixture is not None else tiny_png_1x1
        )
        self._nc: NATS | None = None
        self._sub: Subscription | None = None
        self.calls: list[ImageRequest] = []

    async def start(self) -> None:
        _assert_loopback_url(self._nats_url)
        if self._nc is not None:
            raise RuntimeError("FakeImageWorker already started")
        self._nc = await asyncio.wait_for(
            nats_connect(self._nats_url, allow_reconnect=False, connect_timeout=2),
            timeout=3.0,
        )
        self._sub = await self._nc.subscribe(
            SUBJECTS.image_request,
            queue=SUBJECTS.image_workers,
            cb=self._dispatch,
        )

    async def stop(self) -> None:
        if self._nc is not None and self._nc.is_connected:
            try:
                await asyncio.wait_for(self._nc.drain(), timeout=_DRAIN_TIMEOUT_S)
            except asyncio.TimeoutError:  # pragma: no cover
                log.warning(
                    "FakeImageWorker drain timed out after %.1fs", _DRAIN_TIMEOUT_S
                )
        self._sub = None
        self._nc = None

    async def _dispatch(self, msg: Msg) -> None:
        try:
            req = ImageRequest.model_validate_json(msg.data)
        except ValidationError as exc:
            log.warning("FakeImageWorker dropped malformed request: %s", exc)
            return
        self.calls.append(req)
        if not msg.reply or self._nc is None:
            return
        reply = ImageResponse(
            contract_version=req.contract_version,
            trace_id=req.trace_id,
            issued_at=datetime.now(timezone.utc),
            ok=True,
            request_id=req.request_id,
            image_b64=base64.b64encode(self._reply_fixture).decode("ascii"),
            mime_type=tiny_png_mime,
            width=tiny_png_width,
            height=tiny_png_height,
            engine=req.engine,
            seed_used=req.seed if req.seed is not None else 0,
        )
        await self._nc.publish(msg.reply, reply.model_dump_json().encode())
