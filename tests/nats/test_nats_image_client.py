"""Tests for NatsImageClient (thin 3-layer composition)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.nats.nats_image_client import (
    ImageGenParams,
    ImageUnavailableError,
    NatsImageClient,
)
from lyra.nats.nats_image_codec import ImageResult
from lyra.transport._result import Err, Ok, SanitizedError
from roxabi_contracts.image import ImageResponse


def _make_pool(*, alive: bool = True) -> MagicMock:
    pool = MagicMock()
    pool.is_pool_alive.return_value = alive
    pool.start = AsyncMock()
    pool.stop = AsyncMock()
    return pool


def _ok_response() -> ImageResponse:
    import json

    raw = json.dumps(
        {
            "contract_version": "1",
            "trace_id": "t",
            "issued_at": "2026-04-19T00:00:00+00:00",
            "ok": True,
            "request_id": "r1",
            "blob_ref": {
                "store_key": "test-img",
                "content_hash": "deadbeef",
                "mime": "image/png",
                "size": 3,
                "source": "imagecli",
            },
            "mime_type": "image/png",
            "width": 512,
            "height": 512,
            "engine": "flux2-klein",
            "seed_used": 1,
        }
    ).encode()
    return ImageResponse.model_validate_json(raw)


class TestNatsImageClientAvailability:
    def test_is_available_delegates_to_pool(self) -> None:
        pool = _make_pool(alive=True)
        client = NatsImageClient(pool, MagicMock())
        assert client.is_available() is True

    def test_is_available_false_when_pool_dead(self) -> None:
        pool = _make_pool(alive=False)
        client = NatsImageClient(pool, MagicMock())
        assert client.is_available() is False


class TestNatsImageClientGenerate:
    @pytest.mark.asyncio
    async def test_success_returns_image_response(self) -> None:
        resp = _ok_response()
        pool = _make_pool()
        pool.request_with_routing = AsyncMock(return_value=Ok(b"raw"))
        codec = MagicMock()
        codec.encode.return_value = b"payload"
        codec.decode.return_value = ImageResult(response=resp, error="")
        client = NatsImageClient(pool, codec)
        result = await client.generate(prompt="cat", engine="flux2-klein")
        assert isinstance(result, ImageResponse)
        assert result.ok is True

    @pytest.mark.asyncio
    async def test_codec_error_raises_image_unavailable(self) -> None:
        pool = _make_pool()
        pool.request_with_routing = AsyncMock(return_value=Ok(b"raw"))
        codec = MagicMock()
        codec.encode.return_value = b"payload"
        codec.decode.return_value = ImageResult(
            response=None, error="image.worker_error"
        )
        client = NatsImageClient(pool, codec)
        with pytest.raises(ImageUnavailableError, match="image.worker_error"):
            await client.generate(prompt="cat", engine="flux2-klein")

    @pytest.mark.asyncio
    async def test_pool_err_propagates_via_codec_decode(self) -> None:
        err_result = Err(
            SanitizedError(
                code="pool.no_live_workers", message="NoLiveWorkers", retryable=True
            )
        )
        pool = _make_pool()
        pool.request_with_routing = AsyncMock(return_value=err_result)
        codec = MagicMock()
        codec.encode.return_value = b"payload"
        codec.decode.return_value = ImageResult(
            response=None, error="pool.no_live_workers"
        )
        client = NatsImageClient(pool, codec)
        with pytest.raises(ImageUnavailableError, match="pool.no_live_workers"):
            await client.generate(prompt="cat", engine="flux2-klein")

    @pytest.mark.asyncio
    async def test_encode_called_with_params(self) -> None:
        resp = _ok_response()
        pool = _make_pool()
        pool.request_with_routing = AsyncMock(return_value=Ok(b"raw"))
        codec = MagicMock()
        codec.encode.return_value = b"payload"
        codec.decode.return_value = ImageResult(response=resp, error="")
        params = ImageGenParams(width=1024, height=768)
        client = NatsImageClient(pool, codec)
        await client.generate(prompt="cat", engine="flux2-klein", params=params)
        codec.encode.assert_called_once_with("cat", "flux2-klein", params)

    @pytest.mark.asyncio
    async def test_start_delegates_to_pool(self) -> None:
        pool = _make_pool()
        mock_nc = AsyncMock()
        client = NatsImageClient(pool, MagicMock(), nc=mock_nc)
        await client.start()
        pool.start.assert_awaited_once_with(mock_nc)

    @pytest.mark.asyncio
    async def test_stop_delegates_to_pool(self) -> None:
        pool = _make_pool()
        client = NatsImageClient(pool, MagicMock())
        await client.stop()
        pool.stop.assert_awaited_once()
