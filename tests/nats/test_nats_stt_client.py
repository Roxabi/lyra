"""Tests for NatsSttClient (thin 3-layer composition)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.ports.stt import STTNoiseError, STTUnavailableError, TranscriptionResult
from lyra.nats.nats_stt_client import NatsSttClient
from lyra.transport._result import Err, Ok, SanitizedError


def _make_pool(*, alive: bool = True) -> MagicMock:
    pool = MagicMock()
    pool.is_pool_alive.return_value = alive
    pool.start = AsyncMock()
    pool.stop = AsyncMock()
    return pool


def _make_codec(tr: TranscriptionResult) -> MagicMock:
    codec = MagicMock()
    codec.encode.return_value = b"payload"
    codec.decode.return_value = tr
    return codec


WAV_BYTES = b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x00\x00"


class TestNatsSttClientAvailability:
    def test_is_available_delegates_to_pool(self) -> None:
        pool = _make_pool(alive=True)
        client = NatsSttClient(pool, MagicMock())
        assert client.is_available() is True

    def test_is_available_false_when_pool_dead(self) -> None:
        pool = _make_pool(alive=False)
        client = NatsSttClient(pool, MagicMock())
        assert client.is_available() is False


class TestNatsSttClientTranscribe:
    @pytest.mark.asyncio
    async def test_success_returns_transcription(self) -> None:
        expected = TranscriptionResult(
            text="hello", language="en", duration_seconds=1.0
        )
        pool = _make_pool()
        pool.request_with_routing = AsyncMock(return_value=Ok(b"raw"))
        codec = _make_codec(expected)
        client = NatsSttClient(pool, codec)
        result = await client.transcribe(WAV_BYTES, "audio/wav")
        assert result.text == "hello"
        assert result.language == "en"

    @pytest.mark.asyncio
    async def test_codec_error_raises_stt_unavailable(self) -> None:
        err_tr = TranscriptionResult(
            text="", language="", duration_seconds=0.0, error="stt.worker_error"
        )
        pool = _make_pool()
        pool.request_with_routing = AsyncMock(return_value=Ok(b"raw"))
        codec = _make_codec(err_tr)
        client = NatsSttClient(pool, codec)
        with pytest.raises(STTUnavailableError, match="stt.worker_error"):
            await client.transcribe(WAV_BYTES, "audio/wav")

    @pytest.mark.asyncio
    async def test_pool_err_propagates_via_codec_decode(self) -> None:
        err_result = Err(
            SanitizedError(
                code="pool.no_live_workers", message="NoLiveWorkers", retryable=True
            )
        )
        err_tr = TranscriptionResult(
            text="", language="", duration_seconds=0.0, error="pool.no_live_workers"
        )
        pool = _make_pool()
        pool.request_with_routing = AsyncMock(return_value=err_result)
        codec = _make_codec(err_tr)
        client = NatsSttClient(pool, codec)
        with pytest.raises(STTUnavailableError, match="pool.no_live_workers"):
            await client.transcribe(WAV_BYTES, "audio/wav")

    @pytest.mark.asyncio
    async def test_noise_transcript_raises_noise_error(self) -> None:
        noise_tr = TranscriptionResult(
            text="[music]", language="en", duration_seconds=0.5
        )
        pool = _make_pool()
        pool.request_with_routing = AsyncMock(return_value=Ok(b"raw"))
        codec = _make_codec(noise_tr)
        client = NatsSttClient(pool, codec)
        with pytest.raises(STTNoiseError):
            await client.transcribe(WAV_BYTES, "audio/wav")

    @pytest.mark.asyncio
    async def test_model_passed_to_encode_via_params(self) -> None:
        ok_tr = TranscriptionResult(text="hi", language="fr", duration_seconds=0.3)
        pool = _make_pool()
        pool.request_with_routing = AsyncMock(return_value=Ok(b"raw"))
        codec = _make_codec(ok_tr)
        client = NatsSttClient(pool, codec, model="tiny")
        await client.transcribe(WAV_BYTES, "audio/wav")
        call_args = codec.encode.call_args
        assert call_args.args[0] == WAV_BYTES  # audio
        assert call_args.args[1] == "audio/wav"  # mime
        params = call_args.args[2]
        assert params.model == "tiny"

    @pytest.mark.asyncio
    async def test_start_delegates_to_pool(self) -> None:
        pool = _make_pool()
        mock_nc = AsyncMock()
        client = NatsSttClient(pool, MagicMock(), nc=mock_nc)
        await client.start()
        pool.start.assert_awaited_once_with(mock_nc)

    @pytest.mark.asyncio
    async def test_stop_delegates_to_pool(self) -> None:
        pool = _make_pool()
        client = NatsSttClient(pool, MagicMock())
        await client.stop()
        pool.stop.assert_awaited_once()
