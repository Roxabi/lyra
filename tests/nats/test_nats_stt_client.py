"""Tests for NatsSttClient timeout resolution logic and circuit breaker integration."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.nats.nats_stt_client import NatsSttClient
from lyra.nats.voice_health import WorkerStats
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


def _inject_fresh_worker(
    client: NatsSttClient,
    worker_id: str = "test-worker",
    *,
    vram_used_mb: int = 0,
    vram_total_mb: int = 0,
    active_requests: int = 0,
) -> None:
    """Seed the registry with a fresh worker so routing + freshness gate pass."""
    client._registry.record_heartbeat(
        {
            "worker_id": worker_id,
            "vram_used_mb": vram_used_mb,
            "vram_total_mb": vram_total_mb,
            "active_requests": active_requests,
        }
    )


def _seed_worker_with_age(client: NatsSttClient, worker_id: str, age_s: float) -> None:
    """Insert a worker whose last_heartbeat is ``age_s`` seconds ago."""
    client._registry._workers[worker_id] = WorkerStats(
        worker_id=worker_id,
        last_heartbeat=time.monotonic() - age_s,
    )


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
                "contract_version": "1",
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
                "contract_version": "1",
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
        _seed_worker_with_age(client, "worker-1", 20.0)
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
                "contract_version": "1",
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
        _seed_worker_with_age(client, "worker-1", 5.0)
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
                "contract_version": "1",
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
        _seed_worker_with_age(client, "worker-1", 20.0)
        wav_file = tmp_path / "test.wav"
        wav_file.write_bytes(b"\x00" * 64)
        with pytest.raises(STTUnavailableError, match="no live worker"):
            await client.transcribe(wav_file)
        # Simulate fresh heartbeat arrives
        _seed_worker_with_age(client, "worker-1", 0.0)
        result = await client.transcribe(wav_file)
        assert result.text == "resumed"

    # NOTE: registry-level aliveness / pruning semantics are covered by
    # ``tests/nats/test_voice_health.py`` — no need to duplicate here.


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
        error_payload = json.dumps({"contract_version": "1", "ok": False}).encode()
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
                "contract_version": "1",
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


class TestLoadAwareRouting:
    """Tests for load-aware routing added in #603."""

    @staticmethod
    def _ok_reply() -> MagicMock:
        payload = json.dumps(
            {
                "contract_version": "1",
                "ok": True,
                "text": "hi",
                "language": "en",
                "duration_seconds": 0.1,
            }
        ).encode()
        reply = MagicMock()
        reply.data = payload
        return reply

    @pytest.mark.asyncio
    async def test_single_worker_targets_per_worker_subject(
        self, tmp_path: Path
    ) -> None:
        """With one worker alive, transcribe() targets ``<SUBJECT>.<worker_id>``."""
        mock_nc = AsyncMock()
        mock_nc.request = AsyncMock(return_value=self._ok_reply())
        client = NatsSttClient(nc=mock_nc)
        _inject_fresh_worker(client, "stt-tower-01")
        wav = tmp_path / "a.wav"
        wav.write_bytes(b"\x00" * 16)
        await client.transcribe(wav)
        subject = mock_nc.request.call_args.args[0]
        assert subject == "lyra.voice.stt.request.stt-tower-01"

    @pytest.mark.asyncio
    async def test_empty_registry_raises_without_request(self, tmp_path: Path) -> None:
        """Empty registry → immediate STTUnavailableError, no NATS request attempted."""
        mock_nc = AsyncMock()
        client = NatsSttClient(nc=mock_nc)
        wav = tmp_path / "a.wav"
        wav.write_bytes(b"\x00" * 16)
        with pytest.raises(STTUnavailableError, match="no live worker"):
            await client.transcribe(wav)
        mock_nc.request.assert_not_called()

    @pytest.mark.asyncio
    async def test_picks_least_loaded_by_score(self, tmp_path: Path) -> None:
        """Two workers: heavy VRAM one is skipped, light one receives the request."""
        mock_nc = AsyncMock()
        mock_nc.request = AsyncMock(return_value=self._ok_reply())
        client = NatsSttClient(nc=mock_nc)
        _inject_fresh_worker(
            client, "stt-heavy", vram_used_mb=12000, vram_total_mb=16384
        )
        _inject_fresh_worker(
            client, "stt-light", vram_used_mb=2400, vram_total_mb=16384
        )
        wav = tmp_path / "a.wav"
        wav.write_bytes(b"\x00" * 16)
        await client.transcribe(wav)
        subject = mock_nc.request.call_args.args[0]
        assert subject == "lyra.voice.stt.request.stt-light"

    @pytest.mark.asyncio
    async def test_active_requests_dominate_vram(self, tmp_path: Path) -> None:
        """A busy worker (active_requests>0) loses to an idle higher-VRAM worker."""
        mock_nc = AsyncMock()
        mock_nc.request = AsyncMock(return_value=self._ok_reply())
        client = NatsSttClient(nc=mock_nc)
        _inject_fresh_worker(
            client,
            "stt-busy",
            vram_used_mb=2000,
            vram_total_mb=16384,
            active_requests=2,
        )
        _inject_fresh_worker(
            client,
            "stt-idle-but-fuller",
            vram_used_mb=8000,
            vram_total_mb=16384,
            active_requests=0,
        )
        wav = tmp_path / "a.wav"
        wav.write_bytes(b"\x00" * 16)
        await client.transcribe(wav)
        subject = mock_nc.request.call_args.args[0]
        assert subject == "lyra.voice.stt.request.stt-idle-but-fuller"

    @pytest.mark.asyncio
    async def test_fallback_to_queue_group_on_timeout(self, tmp_path: Path) -> None:
        """Per-worker timeout falls back once to the queue-group subject."""
        mock_nc = AsyncMock()
        call_subjects: list[str] = []

        async def request_mock(subject: str, payload: bytes, timeout: float):
            call_subjects.append(subject)
            if len(call_subjects) == 1:
                raise TimeoutError
            return self._ok_reply()

        mock_nc.request = AsyncMock(side_effect=request_mock)
        client = NatsSttClient(nc=mock_nc)
        _inject_fresh_worker(client, "stt-tower-01")
        wav = tmp_path / "a.wav"
        wav.write_bytes(b"\x00" * 16)
        result = await client.transcribe(wav)
        assert result.text == "hi"
        assert call_subjects == [
            "lyra.voice.stt.request.stt-tower-01",
            "lyra.voice.stt.request",
        ]
        # First timeout should not trip the CB (fallback succeeded).
        assert client._cb._failures == 0

    @pytest.mark.asyncio
    async def test_fallback_timeout_raises_and_records_failure(
        self, tmp_path: Path
    ) -> None:
        """If both preferred AND queue-group timeout, raise + record failure once."""
        mock_nc = AsyncMock()
        mock_nc.request = AsyncMock(side_effect=TimeoutError())
        client = NatsSttClient(nc=mock_nc)
        _inject_fresh_worker(client, "stt-tower-01")
        wav = tmp_path / "a.wav"
        wav.write_bytes(b"\x00" * 16)
        with pytest.raises(STTUnavailableError, match="timeout"):
            await client.transcribe(wav)
        assert mock_nc.request.await_count == 2
        assert client._cb._failures == 1
