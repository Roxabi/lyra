"""NatsImageClient — thin image generation client over WorkerPoolClient + ImageCodec.

Composition (3-layer): NatsImageClient → WorkerPoolClient → NatsTransport.
Per-worker routing via roxabi_contracts.image.per_worker_image.

ImageGenParams and ImageUnavailableError kept for backward compat
(nats_image_codec.py imports ImageGenParams from here).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from roxabi_contracts.image import (
    SUBJECTS,
    ImageHeartbeat,
    ImageRequest,
    ImageResponse,
    per_worker_image,
)  # noqa: F401

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS

    from lyra.nats.nats_image_codec import ImageCodec
    from lyra.transport.worker_pool_client import WorkerPoolClient

log = logging.getLogger(__name__)
__all__ = [
    "ImageGenParams",
    "ImageHeartbeat",
    "ImageRequest",
    "ImageResponse",
    "ImageUnavailableError",
    "NatsImageClient",
    "SUBJECTS",
]


class ImageUnavailableError(Exception):
    """Raised when the image domain cannot satisfy the request."""


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


class NatsImageClient:
    def __init__(
        self,
        pool: "WorkerPoolClient",
        codec: "ImageCodec",
        nc: "NATS | None" = None,
    ) -> None:
        self._pool = pool
        self._codec = codec
        self._nc = nc

    async def start(self) -> None:
        if self._nc is None:
            raise RuntimeError("NatsImageClient.start() called without nc at __init__")
        await self._pool.start(self._nc)

    async def stop(self) -> None:
        await self._pool.stop()

    def is_available(self) -> bool:
        return self._pool.is_pool_alive()

    async def generate(
        self, prompt: str, *, engine: str, params: ImageGenParams | None = None
    ) -> ImageResponse:
        payload = self._codec.encode(prompt, engine, params)
        result = await self._pool.request_with_routing(
            per_worker_image, payload, max_attempts=None
        )
        image_result = self._codec.decode(result)
        if image_result.error:
            raise ImageUnavailableError(image_result.error)
        assert image_result.response is not None
        return image_result.response
