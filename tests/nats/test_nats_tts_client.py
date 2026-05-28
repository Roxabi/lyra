"""Tests for NatsTtsClient (thin 3-layer composition)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.ports.tts import (
    SynthesisResult,
    TtsSynthesisError,
    TtsUnavailableError,
)
from lyra.nats.nats_tts_client import NatsTtsClient
from lyra.transport._result import Err, Ok, SanitizedError
from roxabi_contracts import BlobRef

_FAKE_BLOB = BlobRef(
    store_key="test", content_hash="deadbeef", mime="audio/ogg", size=4, source="test"
)


def _make_pool(*, alive: bool = True) -> MagicMock:
    pool = MagicMock()
    pool.is_pool_alive.return_value = alive
    pool.start = AsyncMock()
    pool.stop = AsyncMock()
    return pool


def _make_codec(synth: SynthesisResult) -> MagicMock:
    codec = MagicMock()
    codec.encode.return_value = b"payload"
    codec.decode.return_value = synth
    return codec


class TestNatsTtsClientAvailability:
    def test_is_available_delegates_to_pool(self) -> None:
        pool = _make_pool(alive=True)
        client = NatsTtsClient(pool, MagicMock())
        assert client.is_available() is True

    def test_is_available_false_when_pool_dead(self) -> None:
        pool = _make_pool(alive=False)
        client = NatsTtsClient(pool, MagicMock())
        assert client.is_available() is False


class TestNatsTtsClientSynthesize:
    @pytest.mark.asyncio
    async def test_success_returns_synth_result(self) -> None:
        expected = SynthesisResult(
            blob_ref=_FAKE_BLOB, mime_type="audio/ogg", duration_ms=100
        )
        pool = _make_pool()
        pool.request_with_routing = AsyncMock(return_value=Ok(b"raw"))
        codec = _make_codec(expected)
        client = NatsTtsClient(pool, codec)
        result = await client.synthesize("hello")
        assert result.blob_ref == _FAKE_BLOB
        assert result.mime_type == "audio/ogg"

    @pytest.mark.asyncio
    async def test_worker_domain_error_raises_tts_synthesis_error(self) -> None:
        """Worker error with unavailable=False → TtsSynthesisError."""
        err_synth = SynthesisResult(
            blob_ref=_FAKE_BLOB,
            mime_type="",
            duration_ms=None,
            error="voice.invalid_voice",
            error_message="Voice Cherry not found",
            error_detail="Available: alloy, echo",
            retryable=False,
            unavailable=False,
        )
        pool = _make_pool()
        pool.request_with_routing = AsyncMock(return_value=Ok(b"raw"))
        codec = _make_codec(err_synth)
        client = NatsTtsClient(pool, codec)
        with pytest.raises(TtsSynthesisError) as exc_info:
            await client.synthesize("hello")
        exc = exc_info.value
        assert exc.code == "voice.invalid_voice"
        assert exc.message == "Voice Cherry not found"
        assert exc.detail == "Available: alloy, echo"
        assert exc.retryable is False

    @pytest.mark.asyncio
    async def test_transport_error_raises_tts_unavailable(self) -> None:
        """Transport Err with unavailable=True → TtsUnavailableError."""
        err_synth = SynthesisResult(
            blob_ref=_FAKE_BLOB,
            mime_type="",
            duration_ms=None,
            error="pool.no_live_workers",
            error_message="NoLiveWorkers",
            retryable=True,
            unavailable=True,
        )
        pool = _make_pool()
        pool.request_with_routing = AsyncMock(return_value=Ok(b"raw"))
        codec = _make_codec(err_synth)
        client = NatsTtsClient(pool, codec)
        with pytest.raises(TtsUnavailableError, match="NoLiveWorkers"):
            await client.synthesize("hello")

    @pytest.mark.asyncio
    async def test_pool_err_propagates_via_codec_decode(self) -> None:
        """Err from pool (unavailable=True) → TtsUnavailableError."""
        err_result = Err(
            SanitizedError(
                code="pool.no_live_workers", message="NoLiveWorkers", retryable=True
            )
        )
        err_synth = SynthesisResult(
            blob_ref=_FAKE_BLOB,
            mime_type="",
            duration_ms=None,
            error="pool.no_live_workers",
            error_message="NoLiveWorkers",
            retryable=True,
            unavailable=True,
        )
        pool = _make_pool()
        pool.request_with_routing = AsyncMock(return_value=err_result)
        codec = _make_codec(err_synth)
        client = NatsTtsClient(pool, codec)
        with pytest.raises(TtsUnavailableError, match="NoLiveWorkers"):
            await client.synthesize("hello")

    @pytest.mark.asyncio
    async def test_worker_error_tts_synthesis_error_str(self) -> None:
        """TtsSynthesisError.__str__ returns 'code: message'."""
        err_synth = SynthesisResult(
            blob_ref=_FAKE_BLOB,
            mime_type="",
            duration_ms=None,
            error="tts.worker_error",
            error_message="generic worker error",
            unavailable=False,
        )
        pool = _make_pool()
        pool.request_with_routing = AsyncMock(return_value=Ok(b"raw"))
        codec = _make_codec(err_synth)
        client = NatsTtsClient(pool, codec)
        with pytest.raises(TtsSynthesisError) as exc_info:
            await client.synthesize("hello")
        assert "tts.worker_error" in str(exc_info.value)
        assert "generic worker error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_encode_called_with_correct_args(self) -> None:
        ok_synth = SynthesisResult(
            blob_ref=_FAKE_BLOB, mime_type="audio/ogg", duration_ms=10
        )
        pool = _make_pool()
        pool.request_with_routing = AsyncMock(return_value=Ok(b"raw"))
        codec = _make_codec(ok_synth)
        client = NatsTtsClient(pool, codec)
        await client.synthesize("hello", language="fr", voice="v1")
        codec.encode.assert_called_once_with(
            "hello",
            agent_tts=None,
            language="fr",
            voice="v1",
            fallback_language=None,
        )

    @pytest.mark.asyncio
    async def test_start_delegates_to_pool(self) -> None:
        pool = _make_pool()
        mock_nc = AsyncMock()
        client = NatsTtsClient(pool, MagicMock(), nc=mock_nc)
        await client.start()
        pool.start.assert_awaited_once_with(mock_nc)

    @pytest.mark.asyncio
    async def test_stop_delegates_to_pool(self) -> None:
        pool = _make_pool()
        client = NatsTtsClient(pool, MagicMock())
        await client.stop()
        pool.stop.assert_awaited_once()
