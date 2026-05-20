"""ImageCodec — pure encode/decode boundary between ImageClient and transport bytes.

No I/O, no network, no NATS imports. encode replays NatsImageClient.generate()
payload-builder. decode handles Result[bytes, SanitizedError] → ImageResult.

CB is NOT touched on decode failure — see spec § "Error path — decode failure".
ImageResult is defined locally: mutating roxabi_contracts.ImageResponse is out
of scope for P1.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import ValidationError

from lyra.transport._result import Err, Result, SanitizedError
from roxabi_contracts.envelope import CONTRACT_VERSION
from roxabi_contracts.image import ImageRequest, ImageResponse

log = logging.getLogger(__name__)


@dataclass
class ImageGenParams:
    """Optional parameters for NatsImageClient.generate."""

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


@dataclass(frozen=True)
class ImageResult:
    """Codec result for image generation. Either response or error is populated."""

    response: ImageResponse | None
    error: str = ""


class ImageCodec:
    """Pure encode/decode for the Image domain.

    encode: builds ImageRequest bytes (mirrors NatsImageClient.generate).
    decode: maps Result[bytes, SanitizedError] → ImageResult; never raises.
    """

    def encode(self, prompt: str, engine: str, params: ImageGenParams | None) -> bytes:
        """Build canonical ImageRequest payload bytes.

        Mirrors NatsImageClient.generate() payload-builder exactly so the wire
        format is bit-for-bit identical.
        """
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
        return request.model_dump_json(exclude_none=True).encode("utf-8")

    def decode(self, result: Result[bytes, SanitizedError]) -> ImageResult:
        """Map transport Result → ImageResult; CB NOT touched on decode failure."""
        if isinstance(result, Err):
            err = result.error
            return ImageResult(response=None, error=err.code)
        try:
            resp = ImageResponse.model_validate_json(result.value)
        except (ValidationError, ValueError) as exc:
            log.warning("ImageCodec.decode: validation error: %r", exc)
            return ImageResult(response=None, error="decode.validation_error")
        if not resp.ok:
            return ImageResult(response=None, error=resp.error or "image.worker_error")
        return ImageResult(response=resp, error="")
