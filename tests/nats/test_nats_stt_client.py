"""Tests for NatsSttClient timeout resolution logic and circuit breaker integration."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.nats.nats_stt_client import NatsSttClient
from lyra.nats.worker_registry import WorkerStats
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
                "trace_id": "tst-trace",
                "issued_at": "2026-04-19T00:00:00+00:00",
                "ok": True,
                "request_id": "r-ok",
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
                "trace_id": "tst-trace",
                "issued_at": "2026-04-19T00:00:00+00:00",
                "ok": True,
                "request_id": "r-ok",
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
                "trace_id": "tst-trace",
                "issued_at": "2026-04-19T00:00:00+00:00",
                "ok": True,
                "request_id": "r-future",
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

    @pytest.mark.asyncio
    async def test_heartbeat_with_wildcard_worker_id_is_dropped(self) -> None:
        """_on_heartbeat with a wildcard worker_id is rejected; registry stays empty."""
        mock_nc = AsyncMock()
        client = NatsSttClient(nc=mock_nc)
        msg = MagicMock()
        msg.data = json.dumps({"worker_id": "evil.worker.*"}).encode()
        await client._on_heartbeat(msg)
        assert client._registry.pick_least_loaded() is None

    @pytest.mark.asyncio
    async def test_heartbeat_with_non_string_worker_id_is_dropped(self) -> None:
        """_on_heartbeat with a non-string worker_id drops the message; no TypeError."""
        mock_nc = AsyncMock()
        client = NatsSttClient(nc=mock_nc)
        msg = MagicMock()
        msg.data = json.dumps({"worker_id": 12345}).encode()
        await client._on_heartbeat(msg)
        assert client._registry.pick_least_loaded() is None


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
                "trace_id": "tst-trace",
                "issued_at": "2026-04-19T00:00:00+00:00",
                "ok": True,
                "request_id": "r-ok",
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
                "trace_id": "tst-trace",
                "issued_at": "2026-04-19T00:00:00+00:00",
                "ok": True,
                "request_id": "r-ok",
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
    # ``tests/nats/test_worker_registry.py`` — no need to duplicate here.


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
        error_payload = json.dumps(
            {
                "contract_version": "1",
                "trace_id": "tst-trace",
                "issued_at": "2026-04-19T00:00:00+00:00",
                "ok": False,
                "request_id": "r-err",
            }
        ).encode()
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
    async def test_ok_false_with_error_field_forwards_message(
        self, tmp_path: Path
    ) -> None:
        """ok=False with a populated `error` field must surface the error string
        in the STTUnavailableError message (not the default "transcription failed")."""
        mock_nc = AsyncMock()
        error_payload = json.dumps(
            {
                "contract_version": "1",
                "trace_id": "tst-trace",
                "issued_at": "2026-04-19T00:00:00+00:00",
                "ok": False,
                "request_id": "r-err",
                "error": "cuda oom",
            }
        ).encode()
        fake_reply = MagicMock()
        fake_reply.data = error_payload
        mock_nc.request = AsyncMock(return_value=fake_reply)
        client = NatsSttClient(nc=mock_nc)
        _inject_fresh_worker(client)
        wav_file = tmp_path / "test.wav"
        wav_file.write_bytes(b"\x00" * 64)

        with pytest.raises(STTUnavailableError, match="cuda oom"):
            await client.transcribe(wav_file)
        assert client._cb._failures == 1

    @pytest.mark.asyncio
    async def test_noise_transcript_raises_noise_error(self, tmp_path: Path) -> None:
        # Arrange — Whisper returns a known noise token
        mock_nc = AsyncMock()
        noise_payload = json.dumps(
            {
                "contract_version": "1",
                "trace_id": "tst-trace",
                "issued_at": "2026-04-19T00:00:00+00:00",
                "ok": True,
                "request_id": "r-noise",
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
                "trace_id": "tst-trace",
                "issued_at": "2026-04-19T00:00:00+00:00",
                "ok": True,
                "request_id": "r-ok",
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
    async def test_timeout_walks_to_second_worker(self, tmp_path: Path) -> None:
        """Per-worker timeout marks worker stale and walks to second worker."""
        mock_nc = AsyncMock()
        call_subjects: list[str] = []

        async def request_mock(subject: str, payload: bytes, timeout: float):
            del payload, timeout  # signature required by AsyncMock side_effect
            call_subjects.append(subject)
            if len(call_subjects) == 1:
                raise TimeoutError
            return self._ok_reply()

        mock_nc.request = AsyncMock(side_effect=request_mock)
        client = NatsSttClient(nc=mock_nc)
        _inject_fresh_worker(client, "stt-01")
        _inject_fresh_worker(client, "stt-02")
        wav = tmp_path / "a.wav"
        wav.write_bytes(b"\x00" * 16)
        result = await client.transcribe(wav)
        assert result.text == "hi"
        # First worker timed out, second succeeded
        assert call_subjects == [
            "lyra.voice.stt.request.stt-01",
            "lyra.voice.stt.request.stt-02",
        ]
        # First worker should be marked stale
        assert "stt-01" not in [w.worker_id for w in client._registry.alive_workers()]
        # No CB failure since second worker succeeded
        assert client._cb._failures == 0

    @pytest.mark.asyncio
    async def test_single_worker_timeout_raises_and_records_failure(
        self, tmp_path: Path
    ) -> None:
        """Single worker timeout -> all workers unresponsive + record failure once."""
        mock_nc = AsyncMock()
        mock_nc.request = AsyncMock(side_effect=TimeoutError())
        client = NatsSttClient(nc=mock_nc)
        _inject_fresh_worker(client, "stt-01")
        wav = tmp_path / "a.wav"
        wav.write_bytes(b"\x00" * 16)
        with pytest.raises(STTUnavailableError, match="all workers unresponsive"):
            await client.transcribe(wav)
        # Only 1 request (per-worker), no queue-group fallback
        assert mock_nc.request.await_count == 1
        assert client._cb._failures == 1


class TestMalformedReply:
    """Pydantic ValidationError on reply MUST surface as STTUnavailableError."""

    @pytest.mark.asyncio
    async def test_malformed_reply_raises_domain_error(self, tmp_path: Path) -> None:
        """ok=True without duration_seconds → SttResponse invariant fails →
        client must translate into STTUnavailableError and record a
        circuit-breaker failure (receive-path anti-drift guard).
        """
        # Arrange
        audio = tmp_path / "sample.wav"
        audio.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")

        mock_nc = AsyncMock()
        # Reply is ok=True but missing duration_seconds — violates
        # SttResponse._enforce_success_invariant (see contracts spec #763
        # drift item #4).
        bad_payload = json.dumps(
            {
                "contract_version": "1",
                "trace_id": "tst-trace",
                "issued_at": "2026-04-19T00:00:00+00:00",
                "ok": True,
                "request_id": "r-bad",
                "text": "hello",
                "language": "en",
                # duration_seconds deliberately omitted
            }
        ).encode()
        fake_reply = MagicMock()
        fake_reply.data = bad_payload
        mock_nc.request = AsyncMock(return_value=fake_reply)

        client = NatsSttClient(nc=mock_nc)
        _inject_fresh_worker(client)
        initial_failures = client._cb._failures

        # Act / Assert
        with pytest.raises(STTUnavailableError, match="schema") as exc_info:
            await client.transcribe(audio)

        # Pin the cause chain to the _parse_reply error-boundary so a future
        # regression where ok=False handling accidentally produces a
        # "schema"-flavored message cannot silently pass this test.
        from pydantic import ValidationError

        assert isinstance(exc_info.value.__cause__, ValidationError)
        assert client._cb._failures == initial_failures + 1

    @pytest.mark.asyncio
    async def test_malformed_json_raises_domain_error(self, tmp_path: Path) -> None:
        """Malformed JSON bytes (not just invariant violations) must also
        surface as STTUnavailableError + CB failure — `_parse_reply` catches
        every pydantic.ValidationError, including JSON-parse errors."""
        audio = tmp_path / "sample.wav"
        audio.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")

        mock_nc = AsyncMock()
        fake_reply = MagicMock()
        fake_reply.data = b"not json {"
        mock_nc.request = AsyncMock(return_value=fake_reply)

        client = NatsSttClient(nc=mock_nc)
        _inject_fresh_worker(client)
        initial_failures = client._cb._failures

        with pytest.raises(STTUnavailableError, match="schema") as exc_info:
            await client.transcribe(audio)

        from pydantic import ValidationError

        assert isinstance(exc_info.value.__cause__, ValidationError)
        assert client._cb._failures == initial_failures + 1


class TestWalkRegistry:
    """Tests for _walk_registry method (ADR-052 registry-authoritative routing).

    _walk_registry iterates over ordered_by_score() candidates, requesting each
    until one succeeds. On timeout/NoRespondersError, mark_stale() is called
    before walking to the next candidate. After all candidates exhausted,
    raises STTUnavailableError chained from the last exception.
    """

    @staticmethod
    def _ok_reply() -> MagicMock:
        payload = json.dumps(
            {
                "contract_version": "1",
                "trace_id": "tst-trace",
                "issued_at": "2026-04-19T00:00:00+00:00",
                "ok": True,
                "request_id": "r-ok",
                "text": "hello",
                "language": "en",
                "duration_seconds": 1.0,
            }
        ).encode()
        reply = MagicMock()
        reply.data = payload
        return reply

    @pytest.mark.asyncio
    async def test_single_worker_success(self) -> None:
        """One worker in registry, successful reply -> returns SttResponse."""
        # Arrange
        mock_nc = AsyncMock()
        mock_nc.request = AsyncMock(return_value=self._ok_reply())
        client = NatsSttClient(nc=mock_nc)
        _inject_fresh_worker(client, "stt-01")
        payload = b'{"test": true}'

        # Act
        result = await client._walk_registry(payload)

        # Assert
        assert result.ok is True
        assert result.text == "hello"
        mock_nc.request.assert_awaited_once()
        subject = mock_nc.request.call_args.args[0]
        assert subject == "lyra.voice.stt.request.stt-01"

    @pytest.mark.asyncio
    async def test_timeout_walks_to_second(self) -> None:
        """W1 times out -> mark_stale called -> W2 succeeds."""
        # Arrange
        mock_nc = AsyncMock()
        call_count = 0

        async def request_mock(subject: str, payload: bytes, timeout: float):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise TimeoutError()
            return self._ok_reply()

        mock_nc.request = AsyncMock(side_effect=request_mock)
        client = NatsSttClient(nc=mock_nc)
        _inject_fresh_worker(client, "stt-01")
        _inject_fresh_worker(client, "stt-02")
        payload = b'{"test": true}'

        # Act
        result = await client._walk_registry(payload)

        # Assert
        assert result.ok is True
        assert mock_nc.request.await_count == 2
        # Verify mark_stale was called on W1
        assert "stt-01" not in [w.worker_id for w in client._registry.alive_workers()]

    @pytest.mark.asyncio
    async def test_no_responders_walks_to_second(self) -> None:
        """W1 raises NoRespondersError -> mark_stale called -> W2 succeeds."""
        # Arrange
        from nats.errors import NoRespondersError

        mock_nc = AsyncMock()
        call_count = 0

        async def request_mock(subject: str, payload: bytes, timeout: float):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise NoRespondersError()
            return self._ok_reply()

        mock_nc.request = AsyncMock(side_effect=request_mock)
        client = NatsSttClient(nc=mock_nc)
        _inject_fresh_worker(client, "stt-01")
        _inject_fresh_worker(client, "stt-02")
        payload = b'{"test": true}'

        # Act
        result = await client._walk_registry(payload)

        # Assert
        assert result.ok is True
        assert mock_nc.request.await_count == 2
        # Verify mark_stale was called on W1
        assert "stt-01" not in [w.worker_id for w in client._registry.alive_workers()]

    @pytest.mark.asyncio
    async def test_all_fail_raises_unavailable(self) -> None:
        """All workers fail -> raises STTUnavailableError chained from last."""
        # Arrange
        mock_nc = AsyncMock()
        mock_nc.request = AsyncMock(side_effect=TimeoutError())
        client = NatsSttClient(nc=mock_nc)
        _inject_fresh_worker(client, "stt-01")
        _inject_fresh_worker(client, "stt-02")
        payload = b'{"test": true}'

        # Act / Assert
        with pytest.raises(
            STTUnavailableError, match="all workers unresponsive"
        ) as exc_info:
            await client._walk_registry(payload)

        # Verify the exception is chained from the last TimeoutError
        assert isinstance(exc_info.value.__cause__, TimeoutError)
        assert mock_nc.request.await_count == 2

    @pytest.mark.asyncio
    async def test_empty_registry_raises_immediately(self) -> None:
        """ordered_by_score() returns [] -> raises STTUnavailableError immediately."""
        # Arrange
        mock_nc = AsyncMock()
        client = NatsSttClient(nc=mock_nc)
        # No workers injected -> registry is empty
        payload = b'{"test": true}'

        # Act / Assert
        with pytest.raises(STTUnavailableError, match="no live worker"):
            await client._walk_registry(payload)

        # No NATS request should be made
        mock_nc.request.assert_not_called()

    @pytest.mark.asyncio
    async def test_circuit_breaker_once_per_exhaustion(self) -> None:
        """record_failure() called once after walk exhaustion, not per candidate."""
        # Arrange
        mock_nc = AsyncMock()
        mock_nc.request = AsyncMock(side_effect=TimeoutError())
        client = NatsSttClient(nc=mock_nc)
        _inject_fresh_worker(client, "stt-01")
        _inject_fresh_worker(client, "stt-02")
        payload = b'{"test": true}'

        # Act
        with pytest.raises(STTUnavailableError):
            await client._walk_registry(payload)

        # Assert - CB failure recorded exactly once (after full exhaustion)
        assert client._cb._failures == 1
        assert mock_nc.request.await_count == 2

    @pytest.mark.asyncio
    async def test_logs_last_error_type(self, caplog: pytest.LogCaptureFixture) -> None:
        """WARNING log includes type(last_exc).__name__ on exhaustion."""
        # Arrange
        mock_nc = AsyncMock()
        mock_nc.request = AsyncMock(side_effect=TimeoutError())
        client = NatsSttClient(nc=mock_nc)
        _inject_fresh_worker(client, "stt-01")
        payload = b'{"test": true}'

        # Act
        with pytest.raises(STTUnavailableError):
            await client._walk_registry(payload)

        # Assert - log should mention the exception type
        assert any(
            "TimeoutError" in record.message and record.levelname == "WARNING"
            for record in caplog.records
        )
