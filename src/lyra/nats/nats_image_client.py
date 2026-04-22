"""NatsImageClient — hub-side NATS request-reply client for image generation.

Maintains a ``WorkerRegistry`` populated from heartbeats, and routes each
generation request to the least-loaded worker via the queue-group subject
``lyra.image.generate.request``. Falls back to ``ImageUnavailableError`` when
the registry is stale, the circuit breaker is open, or the adapter times out.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, NoReturn
from uuid import uuid4

from nats.aio.client import Client as NATS
from pydantic import ValidationError

from lyra.nats.worker_registry import WorkerRegistry
from roxabi_contracts.envelope import CONTRACT_VERSION, ContractEnvelope
from roxabi_contracts.voice.subjects import validate_worker_id
from roxabi_nats.circuit_breaker import NatsCircuitBreaker

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Subject namespace
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Subjects:
    """Frozen namespace of image-domain subject strings.

    Attribute access is pyright-checked: typos fail at type-check time
    rather than silently routing to a wrong subject.
    """

    image_request: Literal["lyra.image.generate.request"] = (
        "lyra.image.generate.request"
    )
    image_heartbeat: Literal["lyra.image.heartbeat"] = "lyra.image.heartbeat"
    image_workers: Literal["IMAGE_WORKERS"] = "IMAGE_WORKERS"


SUBJECTS = _Subjects()


# ---------------------------------------------------------------------------
# Domain exception
# ---------------------------------------------------------------------------


class ImageUnavailableError(Exception):
    """Raised when the image domain cannot satisfy the request."""


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ImageRequest(ContractEnvelope):
    """Outbound image generation request payload."""

    request_id: str
    prompt: str
    engine: str
    negative_prompt: str | None = None
    width: int | None = None
    height: int | None = None
    steps: int | None = None
    guidance: float | None = None
    seed: int | None = None
    format: Literal["png", "jpeg", "webp"] = "png"
    output_mode: Literal["b64", "file"] = "b64"
    lora_path: str | None = None
    lora_scale: float | None = None
    trigger: str | None = None
    embedding_path: str | None = None


class ImageResponse(ContractEnvelope):
    """Inbound image generation response payload."""

    request_id: str
    ok: bool
    image_b64: str | None = None
    file_path: str | None = None
    mime_type: str | None = None
    width: int | None = None
    height: int | None = None
    engine: str | None = None
    seed_used: int | None = None
    error: str | None = None
    error_detail: str | None = None


class ImageHeartbeat(ContractEnvelope):
    """Inbound heartbeat from an image worker satellite."""

    worker_id: str
    service: str
    host: str
    subject: str
    queue_group: str
    ts: float
    engine_loaded: str | None = None
    active_requests: int | None = None
    vram_used_mb: int | None = None
    vram_total_mb: int | None = None


# ---------------------------------------------------------------------------
# Generation parameter bag
# ---------------------------------------------------------------------------


@dataclass
class ImageGenParams:
    """Optional image generation parameters passed to ``NatsImageClient.generate``.

    Separating these from the required fields (prompt, engine) keeps the
    public ``generate()`` signature within the project's 5-argument limit.
    """

    negative_prompt: str | None = None
    width: int | None = None
    height: int | None = None
    steps: int | None = None
    guidance: float | None = None
    seed: int | None = None
    format: Literal["png", "jpeg", "webp"] = field(default="png")
    output_mode: Literal["b64", "file"] = field(default="b64")
    lora_path: str | None = None
    lora_scale: float | None = None
    trigger: str | None = None
    embedding_path: str | None = None


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class NatsImageClient:
    def __init__(self, nc: NATS, *, timeout: float = 120.0) -> None:
        self._nc = nc
        self._timeout = timeout
        self._cb = NatsCircuitBreaker()
        self._registry = WorkerRegistry()
        self._hb_sub = None  # set by start

    async def start(self) -> None:
        """Subscribe to heartbeat subject. Called once after nc is connected."""
        if self._hb_sub is None:
            self._hb_sub = await self._nc.subscribe(
                SUBJECTS.image_heartbeat, cb=self._on_heartbeat
            )

    async def _on_heartbeat(self, msg) -> None:
        try:
            data = json.loads(msg.data)
        except json.JSONDecodeError:
            log.debug("image_client: heartbeat parse error", exc_info=True)
            return
        worker_id = data.get("worker_id")
        if not worker_id:
            log.warning("image_client: heartbeat missing worker_id, ignoring")
            return
        if not isinstance(worker_id, str):
            log.warning(
                "image_client: heartbeat non-string worker_id=%r, ignoring",
                worker_id,
            )
            return
        # Receive-side match for the PUBLISH-path safe-chars enforcement; blocks
        # wildcard-bearing ids (e.g. "evil.worker.*") from polluting the registry
        # for up to the 15 s heartbeat-stale window. Mirrors voice canonical at
        # nats_tts_client.py:70-82.
        try:
            validate_worker_id(worker_id)
        except ValueError:
            log.warning(
                "image_client: heartbeat with unsafe worker_id=%r, ignoring",
                worker_id,
            )
            return
        self._registry.record_heartbeat(data)

    def _parse_reply(self, raw: bytes) -> ImageResponse:
        """Validate a NATS reply against ImageResponse; translate a ValidationError
        into ImageUnavailableError + record a circuit-breaker failure."""
        try:
            return ImageResponse.model_validate_json(raw)
        except ValidationError as exc:
            self._cb.record_failure()
            raise ImageUnavailableError("Image reply failed schema validation") from exc

    async def _send(self, payload: bytes) -> ImageResponse:
        """Send payload to the image request subject."""
        try:
            reply = await self._nc.request(
                SUBJECTS.image_request, payload, timeout=self._timeout
            )
        except TimeoutError as exc:
            self._cb.record_failure()
            raise ImageUnavailableError(
                f"Image adapter timeout after {self._timeout:.0f}s"
            ) from exc
        except Exception as exc:
            self._raise_nats_failure(exc, len(payload) / 1024)
        resp = self._parse_reply(reply.data)
        if not resp.ok:
            self._cb.record_failure()
            raise ImageUnavailableError(resp.error or "Image generation failed")
        return resp

    def _raise_nats_failure(self, exc: Exception, payload_kb: float) -> NoReturn:
        """Convert a NATS request exception to ImageUnavailableError.

        Domain errors (``ImageUnavailableError``) pass through unchanged so
        callers can rely on this being the single translation boundary for
        NATS-transport exceptions.
        """
        if isinstance(exc, ImageUnavailableError):
            raise exc
        if "max_payload" in str(exc).lower() or "MaxPayload" in type(exc).__name__:
            log.error("Image payload too large (%.0f KB)", payload_kb)
            self._cb.record_failure()
            raise ImageUnavailableError("Image request payload too large") from exc
        log.warning("Image adapter unreachable: %s: %s", type(exc).__name__, exc)
        self._cb.record_failure()
        raise ImageUnavailableError("Image adapter unreachable") from exc

    async def generate(
        self,
        prompt: str,
        *,
        engine: str,
        params: ImageGenParams | None = None,
    ) -> ImageResponse:
        """Generate an image via the image adapter satellite.

        Optional generation parameters (size, steps, LoRA, etc.) are passed
        via ``params``; see ``ImageGenParams`` for the full field list.
        """
        preferred = self._registry.pick_least_loaded()
        if preferred is None:
            raise ImageUnavailableError("Image: no live worker (heartbeat stale >15s)")
        if self._cb.is_open():
            raise ImageUnavailableError(
                "Image circuit open — adapter temporarily unavailable"
            )
        p = params or ImageGenParams()
        extra: dict[str, Any] = {
            k: v
            for k, v in {
                "negative_prompt": p.negative_prompt,
                "width": p.width,
                "height": p.height,
                "steps": p.steps,
                "guidance": p.guidance,
                "seed": p.seed,
                "format": p.format,
                "output_mode": p.output_mode,
                "lora_path": p.lora_path,
                "lora_scale": p.lora_scale,
                "trigger": p.trigger,
                "embedding_path": p.embedding_path,
            }.items()
            if v is not None
        }
        request = ImageRequest(
            contract_version=CONTRACT_VERSION,
            trace_id=str(uuid4()),
            issued_at=datetime.now(timezone.utc),
            request_id=str(uuid4()),
            prompt=prompt,
            engine=engine,
            **extra,
        )
        payload = request.model_dump_json(exclude_none=True).encode("utf-8")
        resp = await self._send(payload)
        self._cb.record_success()
        return resp
