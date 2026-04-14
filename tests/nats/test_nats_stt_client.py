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


def _inject_fresh_worker(client: NatsSttClient) -> None:
    """Seed _worker_freshness with a fresh timestamp so freshness gate passes."""
    client._worker_freshness["test-worker"] = time.monotonic()


class TestCircuitBreaker:
    @pytest.mark.asyncio
    async def test_cb_open_blocks_call(self, tmp_path: Path) -> None:
        # Arrange — circuit manually forced open
        mock_nc = AsyncMock()
        client = NatsSttClient(nc=mock_nc)
        _inject_fresh_worker(client)
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
        _inject_fresh_worker(client)
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
        _inject_fresh_worker(client)
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
        mock_nc.request = AsyncMock(side_effect=Exception("NATS: max_payload exceeded"))
        client = NatsSttClient(nc=mock_nc)
        _inject_fresh_worker(client)
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
        success_payload = json.dumps(
            {
                "ok": True,
                "text": "hello",
                "language": "en",
                "duration_seconds": 1.0,
            }
        ).encode()
        fake_reply = MagicMock()
        fake_reply.data = success_payload
        mock_nc.request = AsyncMock(return_value=fake_reply)
        client = NatsSttClient(nc=mock_nc)
        _inject_fresh_worker(client)
        client._cb._failures = 2
        wav_file = tmp_path / "test.wav"
        wav_file.write_bytes(b"\x00" * 64)
        # Act
        result = await client.transcribe(wav_file)
        # Assert
        assert result.text == "hello"
        assert client._cb._failures == 0


class TestContractVersion:
    """Tests for the `contract_version` additive field (ADR-044)."""

    @pytest.mark.asyncio
    async def test_request_payload_emits_contract_version(self, tmp_path: Path) -> None:
        """NatsSttClient.transcribe() stamps contract_version='1' on the request."""
        mock_nc = AsyncMock()
        success_payload = json.dumps(
            {
                "ok": True,
                "text": "hi",
                "language": "en",
                "duration_seconds": 1.0,
            }
        ).encode()
        fake_reply = MagicMock()
        fake_reply.data = success_payload
        mock_nc.request = AsyncMock(return_value=fake_reply)
        client = NatsSttClient(nc=mock_nc)
        _inject_fresh_worker(client)
        wav_file = tmp_path / "test.wav"
        wav_file.write_bytes(b"\x00" * 64)

        await client.transcribe(wav_file)

        payload_bytes = mock_nc.request.call_args.args[1]
        request_dict = json.loads(payload_bytes)
        assert request_dict["contract_version"] == "1"

    @pytest.mark.asyncio
    async def test_reply_with_unknown_contract_version_is_tolerated(
        self, tmp_path: Path
    ) -> None:
        """Hub ignores unknown contract_version values on reply (defensive read)."""
        mock_nc = AsyncMock()
        # Reply carries a version the hub has never seen — must be silently accepted.
        reply_payload = json.dumps(
            {
                "contract_version": "999",
                "ok": True,
                "text": "future",
                "language": "en",
                "duration_seconds": 0.5,
            }
        ).encode()
        fake_reply = MagicMock()
        fake_reply.data = reply_payload
        mock_nc.request = AsyncMock(return_value=fake_reply)
        client = NatsSttClient(nc=mock_nc)
        _inject_fresh_worker(client)
        wav_file = tmp_path / "test.wav"
        wav_file.write_bytes(b"\x00" * 64)

        result = await client.transcribe(wav_file)

        assert result.text == "future"
        assert result.language == "en"
        assert client._cb._failures == 0


class TestSttClientStart:
    """Tests for NatsSttClient.start() lifecycle."""

    @pytest.mark.asyncio
    async def test_start_subscribes_to_heartbeat_subject(self) -> None:
        """start() subscribes to the STT heartbeat subject."""
        mock_nc = AsyncMock()
        client = NatsSttClient(nc=mock_nc)
        await client.start()
        mock_nc.subscribe.assert_awaited_once()
        call_args = mock_nc.subscribe.call_args
        assert call_args[0][0] == "lyra.voice.stt.heartbeat"

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self) -> None:
        """start() called twice only subscribes once."""
        mock_nc = AsyncMock()
        client = NatsSttClient(nc=mock_nc)
        await client.start()
        await client.start()
        assert mock_nc.subscribe.await_count == 1


class TestSttClientFreshness:
    """Tests for freshness tracking gate in NatsSttClient."""

    @pytest.mark.asyncio
    async def test_no_workers_ever_raises_unavailable(self, tmp_path: Path) -> None:
        """transcribe() raises STTUnavailableError when _worker_freshness is empty."""
        mock_nc = AsyncMock()
        client = NatsSttClient(nc=mock_nc)
        wav_file = tmp_path / "test.wav"
        wav_file.write_bytes(b"\x00" * 64)
        with pytest.raises(STTUnavailableError, match="no live worker"):
            await client.transcribe(wav_file)
        mock_nc.request.assert_not_called()

    @pytest.mark.asyncio
    async def test_stale_worker_raises_unavailable(self, tmp_path: Path) -> None:
        """transcribe() raises STTUnavailableError when last heartbeat was >15s ago."""
        mock_nc = AsyncMock()
        client = NatsSttClient(nc=mock_nc)
        client._worker_freshness["worker-1"] = time.monotonic() - 20.0
        wav_file = tmp_path / "test.wav"
        wav_file.write_bytes(b"\x00" * 64)
        with pytest.raises(STTUnavailableError, match="no live worker"):
            await client.transcribe(wav_file)
        mock_nc.request.assert_not_called()

    @pytest.mark.asyncio
    async def test_fresh_worker_proceeds_to_request(self, tmp_path: Path) -> None:
        """transcribe() proceeds past freshness gate when a worker is fresh (<15s)."""
        mock_nc = AsyncMock()
        success_payload = json.dumps(
            {
                "ok": True,
                "text": "hello world",
                "language": "en",
                "duration_seconds": 1.0,
            }
        ).encode()
        fake_reply = MagicMock()
        fake_reply.data = success_payload
        mock_nc.request = AsyncMock(return_value=fake_reply)
        client = NatsSttClient(nc=mock_nc)
        client._worker_freshness["worker-1"] = time.monotonic() - 5.0
        wav_file = tmp_path / "test.wav"
        wav_file.write_bytes(b"\x00" * 64)
        result = await client.transcribe(wav_file)
        assert result.text == "hello world"
        mock_nc.request.assert_called_once()

    @pytest.mark.asyncio
    async def test_freshness_gate_before_circuit_breaker(self, tmp_path: Path) -> None:
        """STTUnavailableError from freshness gate does NOT trip circuit breaker."""
        mock_nc = AsyncMock()
        client = NatsSttClient(nc=mock_nc)
        # _worker_freshness is empty — freshness gate fires first
        wav_file = tmp_path / "test.wav"
        wav_file.write_bytes(b"\x00" * 64)
        with pytest.raises(STTUnavailableError, match="no live worker"):
            await client.transcribe(wav_file)
        assert client._cb._failures == 0

    @pytest.mark.asyncio
    async def test_heartbeat_resumes_reenables_worker(self, tmp_path: Path) -> None:
        """After stale, a new heartbeat re-enables the worker immediately."""
        mock_nc = AsyncMock()
        success_payload = json.dumps(
            {
                "ok": True,
                "text": "resumed",
                "language": "en",
                "duration_seconds": 1.0,
            }
        ).encode()
        fake_reply = MagicMock()
        fake_reply.data = success_payload
        mock_nc.request = AsyncMock(return_value=fake_reply)
        client = NatsSttClient(nc=mock_nc)
        # First: stale
        client._worker_freshness["worker-1"] = time.monotonic() - 20.0
        wav_file = tmp_path / "test.wav"
        wav_file.write_bytes(b"\x00" * 64)
        with pytest.raises(STTUnavailableError, match="no live worker"):
            await client.transcribe(wav_file)
        # Simulate fresh heartbeat arrives
        client._worker_freshness["worker-1"] = time.monotonic()
        result = await client.transcribe(wav_file)
        assert result.text == "resumed"

    def test_any_worker_alive_true_within_ttl(self) -> None:
        """_any_worker_alive() returns True when a worker has a recent timestamp."""
        mock_nc = MagicMock()
        client = NatsSttClient(nc=mock_nc)
        client._worker_freshness["worker-1"] = time.monotonic() - 5.0
        assert client._any_worker_alive() is True

    def test_any_worker_alive_false_when_stale(self) -> None:
        """_any_worker_alive() returns False when all workers are >15s stale."""
        mock_nc = MagicMock()
        client = NatsSttClient(nc=mock_nc)
        client._worker_freshness["worker-1"] = time.monotonic() - 20.0
        assert client._any_worker_alive() is False

    def test_any_worker_alive_true_with_mixed_freshness(self) -> None:
        """_any_worker_alive() returns True when at least one worker is fresh."""
        mock_nc = MagicMock()
        client = NatsSttClient(nc=mock_nc)
        client._worker_freshness["stale-worker"] = time.monotonic() - 20.0
        client._worker_freshness["fresh-worker"] = time.monotonic() - 5.0
        assert client._any_worker_alive() is True

    def test_stale_entries_pruned_in_any_worker_alive(self) -> None:
        """_any_worker_alive() evicts entries older than TTL*2."""
        mock_nc = MagicMock()
        client = NatsSttClient(nc=mock_nc)
        client._worker_freshness["ancient"] = time.monotonic() - 35.0  # > 15*2
        client._worker_freshness["fresh"] = time.monotonic() - 5.0
        client._any_worker_alive()
        assert "ancient" not in client._worker_freshness
        assert "fresh" in client._worker_freshness


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
        _inject_fresh_worker(client)
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
        noise_payload = json.dumps(
            {
                "ok": True,
                "text": "[music]",
                "language": "en",
                "duration_seconds": 0.5,
            }
        ).encode()
        fake_reply = MagicMock()
        fake_reply.data = noise_payload
        mock_nc.request = AsyncMock(return_value=fake_reply)
        client = NatsSttClient(nc=mock_nc)
        _inject_fresh_worker(client)
        wav_file = tmp_path / "test.wav"
        wav_file.write_bytes(b"\x00" * 64)
        # Act / Assert
        with pytest.raises(STTNoiseError):
            await client.transcribe(wav_file)
        # Noise is NOT a CB failure — record_success() runs before the noise check
        assert client._cb._failures == 0
