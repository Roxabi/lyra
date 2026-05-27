"""NatsImageClient — thin image generation client over WorkerPoolClient + ImageCodec.

Composition (3-layer): NatsImageClient → WorkerPoolClient → NatsTransport.
Per-worker routing via roxabi_contracts.image.per_worker_image.

ImageGenParams owned by nats_image_codec.py (codec purity, #1278 review S3).
Re-exported here for backward compat — prefer importing from the codec in new code.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from lyra.nats.nats_image_codec import ImageGenParams
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
    "ImageGenParams",  # re-exported from nats_image_codec for backward compat
    "ImageHeartbeat",
    "ImageRequest",
    "ImageResponse",
    "ImageUnavailableError",
    "NatsImageClient",
    "SUBJECTS",
]


class ImageUnavailableError(Exception):
    """Raised when the image domain cannot satisfy the request."""


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
