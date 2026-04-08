"""Tests for NatsSttClient timeout resolution logic and circuit breaker integration."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.nats.nats_stt_client import NatsSttClient
from lyra.stt import STTNoiseError, STTUnavailableError


@pytest.fixture()
def mock_nc() -> MagicMock:
    return MagicMock()


class TestTimeoutResolution:
    def test_default_timeout_is_15s(
        self, mock_nc: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("LYRA_STT_TIMEOUT", raising=False)
        client = NatsSttClient(nc=mock_nc)
        assert client._timeout == 15.0

    def test_env_var_overrides_default(
        self, mock_nc: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LYRA_STT_TIMEOUT", "30")
        client = NatsSttClient(nc=mock_nc)
        assert client._timeout == 30.0

    def test_explicit_timeout_wins_over_env_var(
        self, mock_nc: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LYRA_STT_TIMEOUT", "99")
        client = NatsSttClient(nc=mock_nc, timeout=5.0)
        assert client._timeout == 5.0

    def test_malformed_env_var_falls_back_to_default(
        self, mock_nc: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LYRA_STT_TIMEOUT", "not-a-number")
        client = NatsSttClient(nc=mock_nc)
        assert client._timeout == 15.0

    def test_empty_env_var_falls_back_to_default(
        self, mock_nc: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LYRA_STT_TIMEOUT", "")
        client = NatsSttClient(nc=mock_nc)
        assert client._timeout == 15.0

    def test_zero_timeout_falls_back_to_default(self, mock_nc: MagicMock) -> None:
        client = NatsSttClient(nc=mock_nc, timeout=0.0)
        assert client._timeout == 15.0

    def test_negative_timeout_falls_back_to_default(self, mock_nc: MagicMock) -> None:
        client = NatsSttClient(nc=mock_nc, timeout=-1.0)
        assert client._timeout == 15.0

    def test_inf_timeout_falls_back_to_default(self, mock_nc: MagicMock) -> None:
        client = NatsSttClient(nc=mock_nc, timeout=float("inf"))
        assert client._timeout == 15.0

    def test_boundary_min_timeout_accepted(self, mock_nc: MagicMock) -> None:
        client = NatsSttClient(nc=mock_nc, timeout=1.0)
        assert client._timeout == 1.0

    def test_boundary_max_timeout_accepted(self, mock_nc: MagicMock) -> None:
        client = NatsSttClient(nc=mock_nc, timeout=300.0)
        assert client._timeout == 300.0

    def test_env_var_zero_falls_back_to_default(
        self, mock_nc: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LYRA_STT_TIMEOUT", "0")
        client = NatsSttClient(nc=mock_nc)
        assert client._timeout == 15.0


class TestCircuitBreaker:
    @pytest.mark.asyncio
    async def test_cb_open_blocks_call(self, tmp_path: Path) -> None:
        # Arrange — circuit manually forced open
        mock_nc = AsyncMock()
        client = NatsSttClient(nc=mock_nc)
        client._cb._open_until = time.monotonic() + 100.0
        wav_file = tmp_path / "test.wav"
        wav_file.write_bytes(b"")
        # Act / Assert
        with pytest.raises(STTUnavailableError, match="circuit open"):
            await client.transcribe(wav_file)
        mock_nc.request.assert_not_called()

    @pytest.mark.asyncio
    async def test_failure_records_on_timeout(self, tmp_path: Path) -> None:
        # Arrange
        mock_nc = AsyncMock()
        mock_nc.request = AsyncMock(side_effect=TimeoutError())
        client = NatsSttClient(nc=mock_nc)
        wav_file = tmp_path / "test.wav"
        wav_file.write_bytes(b"\x00" * 64)
        # Act
        with pytest.raises(STTUnavailableError):
            await client.transcribe(wav_file)
        # Assert
        assert client._cb._failures == 1

    @pytest.mark.asyncio
    async def test_failure_records_on_unreachable(self, tmp_path: Path) -> None:
        # Arrange
        mock_nc = AsyncMock()
        mock_nc.request = AsyncMock(side_effect=Exception("NATS error"))
        client = NatsSttClient(nc=mock_nc)
        wav_file = tmp_path / "test.wav"
        wav_file.write_bytes(b"\x00" * 64)
        # Act
        with pytest.raises(STTUnavailableError):
            await client.transcribe(wav_file)
        # Assert
        assert client._cb._failures == 1

    @pytest.mark.asyncio
    async def test_failure_records_on_max_payload(self, tmp_path: Path) -> None:
        # Arrange
        mock_nc = AsyncMock()
        mock_nc.request = AsyncMock(
            side_effect=Exception("NATS: max_payload exceeded")
        )
        client = NatsSttClient(nc=mock_nc)
        wav_file = tmp_path / "test.wav"
        wav_file.write_bytes(b"\x00" * 64)
        # Act
        with pytest.raises(STTUnavailableError, match="payload too large"):
            await client.transcribe(wav_file)
        # Assert
        assert client._cb._failures == 1

    @pytest.mark.asyncio
    async def test_success_clears_failures(self, tmp_path: Path) -> None:
        # Arrange — pre-inject 2 failures
        mock_nc = AsyncMock()
        success_payload = json.dumps({
            "ok": True,
            "text": "hello",
            "language": "en",
            "duration_seconds": 1.0,
        }).encode()
        fake_reply = MagicMock()
        fake_reply.data = success_payload
        mock_nc.request = AsyncMock(return_value=fake_reply)
        client = NatsSttClient(nc=mock_nc)
        client._cb._failures = 2
        wav_file = tmp_path / "test.wav"
        wav_file.write_bytes(b"\x00" * 64)
        # Act
        result = await client.transcribe(wav_file)
        # Assert
        assert result.text == "hello"
        assert client._cb._failures == 0


class TestTranscribeResponseParsing:
    """Tests for NATS response parsing in NatsSttClient.transcribe().

    Distinct from TestCircuitBreaker — these tests verify how the client
    interprets specific response payloads (ok=false, noise tokens), not
    circuit-breaker state transitions.
    """

    @pytest.mark.asyncio
    async def test_ok_false_raises_unavailable(self, tmp_path: Path) -> None:
        # Arrange
        mock_nc = AsyncMock()
        error_payload = json.dumps({"ok": False}).encode()
        fake_reply = MagicMock()
        fake_reply.data = error_payload
        mock_nc.request = AsyncMock(return_value=fake_reply)
        client = NatsSttClient(nc=mock_nc)
        wav_file = tmp_path / "test.wav"
        wav_file.write_bytes(b"\x00" * 64)
        # Act / Assert
        with pytest.raises(STTUnavailableError, match="transcription failed"):
            await client.transcribe(wav_file)
        assert client._cb._failures == 1

    @pytest.mark.asyncio
    async def test_noise_transcript_raises_noise_error(self, tmp_path: Path) -> None:
        # Arrange — Whisper returns a known noise token
        mock_nc = AsyncMock()
        noise_payload = json.dumps({
            "ok": True,
            "text": "[music]",
            "language": "en",
            "duration_seconds": 0.5,
        }).encode()
        fake_reply = MagicMock()
        fake_reply.data = noise_payload
        mock_nc.request = AsyncMock(return_value=fake_reply)
        client = NatsSttClient(nc=mock_nc)
        wav_file = tmp_path / "test.wav"
        wav_file.write_bytes(b"\x00" * 64)
        # Act / Assert
        with pytest.raises(STTNoiseError):
            await client.transcribe(wav_file)
        # Noise is NOT a CB failure — record_success() runs before the noise check
        assert client._cb._failures == 0
