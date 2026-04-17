"""Tests for FfmpegConverter (lyra.integrations.audio)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lyra.integrations.audio import FfmpegConverter
from lyra.integrations.base import AudioConversionFailed, AudioConverter


class TestFfmpegConverterProtocol:
    def test_implements_audio_converter(self):
        assert isinstance(FfmpegConverter(), AudioConverter)


class TestFfmpegConverterConvert:
    @pytest.mark.asyncio
    async def test_happy_path_completes(self, tmp_path: Path):
        wav = tmp_path / "test.wav"
        ogg = tmp_path / "test.ogg"
        wav.write_bytes(b"fake")

        proc = MagicMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"", b""))

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            await FfmpegConverter().convert_wav_to_ogg(wav, ogg)

    @pytest.mark.asyncio
    async def test_calls_ffmpeg_with_correct_args(self, tmp_path: Path):
        wav = tmp_path / "test.wav"
        ogg = tmp_path / "test.ogg"
        wav.write_bytes(b"fake")
        calls: list[list] = []

        proc = MagicMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"", b""))

        async def fake_exec(*args, **kwargs):
            calls.append(list(args))
            return proc

        with patch("asyncio.create_subprocess_exec", new=fake_exec):
            await FfmpegConverter().convert_wav_to_ogg(wav, ogg)

        assert calls[0][0] == "ffmpeg"
        assert "-c:a" in calls[0]
        assert "libopus" in calls[0]
        assert str(wav) in calls[0]
        assert str(ogg) in calls[0]

    @pytest.mark.asyncio
    async def test_file_not_found_raises(self, tmp_path: Path):
        wav = tmp_path / "test.wav"
        ogg = tmp_path / "test.ogg"

        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            with pytest.raises(AudioConversionFailed) as exc_info:
                await FfmpegConverter().convert_wav_to_ogg(wav, ogg)
            assert exc_info.value.reason == "not_available"

    @pytest.mark.asyncio
    async def test_nonzero_exit_raises(self, tmp_path: Path):
        wav = tmp_path / "test.wav"
        ogg = tmp_path / "test.ogg"

        proc = MagicMock()
        proc.returncode = 1
        proc.communicate = AsyncMock(return_value=(b"", b"error"))

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            with pytest.raises(AudioConversionFailed) as exc_info:
                await FfmpegConverter().convert_wav_to_ogg(wav, ogg)
            assert exc_info.value.reason == "subprocess_error"

    @pytest.mark.asyncio
    async def test_timeout_raises(self, tmp_path: Path):
        wav = tmp_path / "test.wav"
        ogg = tmp_path / "test.ogg"

        proc = MagicMock()
        proc.kill = MagicMock()
        proc.wait = AsyncMock()

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            with patch(
                "lyra.integrations.audio.asyncio.wait_for",
                side_effect=asyncio.TimeoutError,
            ):
                with pytest.raises(AudioConversionFailed) as exc_info:
                    await FfmpegConverter().convert_wav_to_ogg(wav, ogg)
                assert exc_info.value.reason == "timeout"
