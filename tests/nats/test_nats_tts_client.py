"""Tests for NatsTtsClient circuit breaker integration."""

from __future__ import annotations

import base64
import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.agent.agent_config import AgentTTSConfig
from lyra.nats.nats_tts_client import _TTS_CONFIG_FIELDS, NatsTtsClient
from lyra.nats.worker_registry import WorkerStats
from lyra.tts import TtsUnavailableError


def _inject_fresh_worker(
    client: NatsTtsClient,
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


def _seed_worker_with_age(client: NatsTtsClient, worker_id: str, age_s: float) -> None:
    """Insert a worker whose last_heartbeat is ``age_s`` seconds ago."""
    client._registry._workers[worker_id] = WorkerStats(
        worker_id=worker_id,
        last_heartbeat=time.monotonic() - age_s,
    )


class TestCircuitBreaker:
    @pytest.mark.asyncio
    async def test_cb_open_blocks_synthesize(self) -> None:
        # Arrange — circuit manually forced open
        mock_nc = AsyncMock()
        client = NatsTtsClient(nc=mock_nc)
        _inject_fresh_worker(client)
        client._cb._open_until = time.monotonic() + 100.0
        # Act / Assert
        with pytest.raises(TtsUnavailableError, match="circuit open"):
            await client.synthesize("hello")
        mock_nc.request.assert_not_called()

    @pytest.mark.asyncio
    async def test_failure_records_on_timeout(self) -> None:
        # Arrange
        mock_nc = AsyncMock()
        mock_nc.request = AsyncMock(side_effect=TimeoutError())
        client = NatsTtsClient(nc=mock_nc)
        _inject_fresh_worker(client)
        # Act
        with pytest.raises(TtsUnavailableError):
            await client.synthesize("hello")
        # Assert
        assert client._cb._failures == 1

    @pytest.mark.asyncio
    async def test_failure_records_on_unreachable(self) -> None:
        # Arrange
        mock_nc = AsyncMock()
        mock_nc.request = AsyncMock(side_effect=Exception("NATS error"))
        client = NatsTtsClient(nc=mock_nc)
        _inject_fresh_worker(client)
        # Act
        with pytest.raises(TtsUnavailableError):
            await client.synthesize("hello")
        # Assert
        assert client._cb._failures == 1

    @pytest.mark.asyncio
    async def test_failure_records_on_max_payload(self) -> None:
        # Arrange
        mock_nc = AsyncMock()
        mock_nc.request = AsyncMock(side_effect=Exception("NATS: max_payload exceeded"))
        client = NatsTtsClient(nc=mock_nc)
        _inject_fresh_worker(client)
        # Act
        with pytest.raises(TtsUnavailableError, match="payload too large"):
            await client.synthesize("hello")
        # Assert
        assert client._cb._failures == 1

    @pytest.mark.asyncio
    async def test_ok_false_raises_unavailable(self) -> None:
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
        client = NatsTtsClient(nc=mock_nc)
        _inject_fresh_worker(client)
        # Act / Assert
        with pytest.raises(TtsUnavailableError, match="synthesis failed"):
            await client.synthesize("hello")
        assert client._cb._failures == 1

    @pytest.mark.asyncio
    async def test_ok_false_with_error_field_forwards_message(self) -> None:
        """ok=False with a populated `error` field must surface the error string
        in the TtsUnavailableError message (not the default "synthesis failed")."""
        mock_nc = AsyncMock()
        error_payload = json.dumps(
            {
                "contract_version": "1",
                "trace_id": "tst-trace",
                "issued_at": "2026-04-19T00:00:00+00:00",
                "ok": False,
                "request_id": "r-err",
                "error": "worker OOM",
            }
        ).encode()
        fake_reply = MagicMock()
        fake_reply.data = error_payload
        mock_nc.request = AsyncMock(return_value=fake_reply)
        client = NatsTtsClient(nc=mock_nc)
        _inject_fresh_worker(client)

        with pytest.raises(TtsUnavailableError, match="worker OOM"):
            await client.synthesize("hello")
        assert client._cb._failures == 1

    @pytest.mark.asyncio
    async def test_agent_tts_fields_forwarded_in_request(self) -> None:
        # Arrange — agent_tts with engine + speed set
        mock_nc = AsyncMock()
        success_payload = json.dumps(
            {
                "contract_version": "1",
                "trace_id": "tst-trace",
                "issued_at": "2026-04-19T00:00:00+00:00",
                "ok": True,
                "request_id": "r-agent",
                "audio_b64": base64.b64encode(b"fake").decode(),
                "mime_type": "audio/ogg",
                "duration_ms": 1000,
            }
        ).encode()
        fake_reply = MagicMock()
        fake_reply.data = success_payload
        mock_nc.request = AsyncMock(return_value=fake_reply)
        client = NatsTtsClient(nc=mock_nc)
        _inject_fresh_worker(client)

        agent_tts = AgentTTSConfig(engine="chatterbox", speed="1.2")
        # Act
        await client.synthesize("hello", agent_tts=agent_tts)
        # Assert — the payload passed to nc.request contains the agent fields
        assert mock_nc.request.await_count == 1
        call_args = mock_nc.request.call_args
        payload_bytes = call_args.args[1]
        request_dict = json.loads(payload_bytes)
        assert request_dict["engine"] == "chatterbox"
        assert request_dict["speed"] == "1.2"
        # contract_version is always stamped (ADR-044)
        assert request_dict["contract_version"] == "1"
        # All unset fields (None) must be absent from the NATS payload
        none_fields = [f for f in _TTS_CONFIG_FIELDS if f not in ("engine", "speed")]
        assert all(f not in request_dict for f in none_fields)

    @pytest.mark.asyncio
    async def test_success_clears_failures(self) -> None:
        # Arrange — pre-inject 2 failures
        mock_nc = AsyncMock()
        success_payload = json.dumps(
            {
                "contract_version": "1",
                "trace_id": "tst-trace",
                "issued_at": "2026-04-19T00:00:00+00:00",
                "ok": True,
                "request_id": "r-success",
                "audio_b64": base64.b64encode(b"fake").decode(),
                "mime_type": "audio/ogg",
                "duration_ms": 1000,
            }
        ).encode()
        fake_reply = MagicMock()
        fake_reply.data = success_payload
        mock_nc.request = AsyncMock(return_value=fake_reply)
        client = NatsTtsClient(nc=mock_nc)
        _inject_fresh_worker(client)
        client._cb._failures = 2
        # Act
        result = await client.synthesize("hello")
        # Assert
        assert result.audio_bytes == b"fake"
        assert client._cb._failures == 0


class TestContractVersion:
    """Tests for the `contract_version` additive field (ADR-044)."""

    @pytest.mark.asyncio
    async def test_request_payload_emits_contract_version(self) -> None:
        """NatsTtsClient.synthesize() stamps contract_version='1' on the request."""
        mock_nc = AsyncMock()
        success_payload = json.dumps(
            {
                "contract_version": "1",
                "trace_id": "tst-trace",
                "issued_at": "2026-04-19T00:00:00+00:00",
                "ok": True,
                "request_id": "r-cv",
                "audio_b64": base64.b64encode(b"hi").decode(),
                "mime_type": "audio/ogg",
                "duration_ms": 500,
            }
        ).encode()
        fake_reply = MagicMock()
        fake_reply.data = success_payload
        mock_nc.request = AsyncMock(return_value=fake_reply)
        client = NatsTtsClient(nc=mock_nc)
        _inject_fresh_worker(client)

        await client.synthesize("hello")

        payload_bytes = mock_nc.request.call_args.args[1]
        request_dict = json.loads(payload_bytes)
        assert request_dict["contract_version"] == "1"

    @pytest.mark.asyncio
    async def test_reply_with_unknown_contract_version_is_tolerated(self) -> None:
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
                "audio_b64": base64.b64encode(b"future").decode(),
                "mime_type": "audio/ogg",
                "duration_ms": 500,
            }
        ).encode()
        fake_reply = MagicMock()
        fake_reply.data = reply_payload
        mock_nc.request = AsyncMock(return_value=fake_reply)
        client = NatsTtsClient(nc=mock_nc)
        _inject_fresh_worker(client)

        result = await client.synthesize("hello")

        assert result.audio_bytes == b"future"
        assert result.mime_type == "audio/ogg"
        assert client._cb._failures == 0


class TestTtsClientStart:
    """Tests for NatsTtsClient.start() lifecycle."""

    @pytest.mark.asyncio
    async def test_start_subscribes_to_heartbeat_subject(self) -> None:
        """start() subscribes to the TTS heartbeat subject."""
        mock_nc = AsyncMock()
        client = NatsTtsClient(nc=mock_nc)
        await client.start()
        mock_nc.subscribe.assert_awaited_once()
        call_args = mock_nc.subscribe.call_args
        assert call_args[0][0] == "lyra.voice.tts.heartbeat"

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self) -> None:
        """start() called twice only subscribes once."""
        mock_nc = AsyncMock()
        client = NatsTtsClient(nc=mock_nc)
        await client.start()
        await client.start()
        assert mock_nc.subscribe.await_count == 1

    @pytest.mark.asyncio
    async def test_heartbeat_with_wildcard_worker_id_is_dropped(self) -> None:
        """_on_heartbeat with a wildcard worker_id is rejected; registry stays empty."""
        mock_nc = AsyncMock()
        client = NatsTtsClient(nc=mock_nc)
        msg = MagicMock()
        msg.data = json.dumps({"worker_id": "evil.worker.*"}).encode()
        await client._on_heartbeat(msg)
        assert client._registry.pick_least_loaded() is None

    @pytest.mark.asyncio
    async def test_heartbeat_with_non_string_worker_id_is_dropped(self) -> None:
        """_on_heartbeat with a non-string worker_id drops the message; no TypeError."""
        mock_nc = AsyncMock()
        client = NatsTtsClient(nc=mock_nc)
        msg = MagicMock()
        msg.data = json.dumps({"worker_id": 12345}).encode()
        await client._on_heartbeat(msg)
        assert client._registry.pick_least_loaded() is None


class TestTtsClientFreshness:
    """Tests for freshness tracking gate in NatsTtsClient."""

    @pytest.mark.asyncio
    async def test_no_workers_ever_raises_unavailable(self) -> None:
        """synthesize() raises TtsUnavailableError when _worker_freshness is empty."""
        mock_nc = AsyncMock()
        client = NatsTtsClient(nc=mock_nc)
        with pytest.raises(TtsUnavailableError, match="no live worker"):
            await client.synthesize("hello")
        mock_nc.request.assert_not_called()

    @pytest.mark.asyncio
    async def test_stale_worker_raises_unavailable(self) -> None:
        """synthesize() raises TtsUnavailableError when last heartbeat was >15s ago."""
        mock_nc = AsyncMock()
        client = NatsTtsClient(nc=mock_nc)
        _seed_worker_with_age(client, "worker-1", 20.0)
        with pytest.raises(TtsUnavailableError, match="no live worker"):
            await client.synthesize("hello")
        mock_nc.request.assert_not_called()

    @pytest.mark.asyncio
    async def test_fresh_worker_proceeds_to_request(self) -> None:
        """synthesize() proceeds past freshness gate when a worker is fresh (<15s)."""
        mock_nc = AsyncMock()
        mock_response = MagicMock()
        mock_response.data = json.dumps(
            {
                "contract_version": "1",
                "trace_id": "tst-trace",
                "issued_at": "2026-04-19T00:00:00+00:00",
                "ok": True,
                "request_id": "r-fresh",
                "audio_b64": base64.b64encode(b"audio").decode(),
                "mime_type": "audio/ogg",
                "duration_ms": 1000,
            }
        ).encode()
        mock_nc.request = AsyncMock(return_value=mock_response)
        client = NatsTtsClient(nc=mock_nc)
        _seed_worker_with_age(client, "worker-1", 5.0)
        result = await client.synthesize("hello")
        assert result.audio_bytes == b"audio"
        mock_nc.request.assert_called_once()

    @pytest.mark.asyncio
    async def test_freshness_gate_before_circuit_breaker(self) -> None:
        """TtsUnavailableError from freshness gate does NOT trip circuit breaker."""
        mock_nc = AsyncMock()
        client = NatsTtsClient(nc=mock_nc)
        # _worker_freshness is empty — freshness gate fires first
        with pytest.raises(TtsUnavailableError, match="no live worker"):
            await client.synthesize("hello")
        assert client._cb._failures == 0

    @pytest.mark.asyncio
    async def test_heartbeat_resumes_reenables_worker(self) -> None:
        """After stale, a new heartbeat re-enables the worker immediately."""
        mock_nc = AsyncMock()
        mock_response = MagicMock()
        mock_response.data = json.dumps(
            {
                "contract_version": "1",
                "trace_id": "tst-trace",
                "issued_at": "2026-04-19T00:00:00+00:00",
                "ok": True,
                "request_id": "r-resume",
                "audio_b64": base64.b64encode(b"audio").decode(),
                "mime_type": "audio/ogg",
                "duration_ms": 1000,
            }
        ).encode()
        mock_nc.request = AsyncMock(return_value=mock_response)
        client = NatsTtsClient(nc=mock_nc)
        # First: stale
        _seed_worker_with_age(client, "worker-1", 20.0)
        with pytest.raises(TtsUnavailableError, match="no live worker"):
            await client.synthesize("hello")
        # Simulate fresh heartbeat arrives
        _seed_worker_with_age(client, "worker-1", 0.0)
        result = await client.synthesize("hello")
        assert result.audio_bytes == b"audio"

    # NOTE: registry-level aliveness / pruning semantics are covered by
    # ``tests/nats/test_worker_registry.py`` — no need to duplicate here.


class TestTtsLoadAwareRouting:
    """Tests for TTS load-aware routing added in #603."""

    @staticmethod
    def _ok_reply() -> MagicMock:
        payload = json.dumps(
            {
                "contract_version": "1",
                "trace_id": "tst-trace",
                "issued_at": "2026-04-19T00:00:00+00:00",
                "ok": True,
                "request_id": "r-ok",
                "audio_b64": base64.b64encode(b"fake").decode(),
                "mime_type": "audio/ogg",
                "duration_ms": 1000,
            }
        ).encode()
        reply = MagicMock()
        reply.data = payload
        return reply

    @pytest.mark.asyncio
    async def test_single_worker_targets_per_worker_subject(self) -> None:
        mock_nc = AsyncMock()
        mock_nc.request = AsyncMock(return_value=self._ok_reply())
        client = NatsTtsClient(nc=mock_nc)
        _inject_fresh_worker(client, "tts-tower-01")
        await client.synthesize("hi")
        subject = mock_nc.request.call_args.args[0]
        assert subject == "lyra.voice.tts.request.tts-tower-01"

    @pytest.mark.asyncio
    async def test_picks_least_loaded_by_score(self) -> None:
        mock_nc = AsyncMock()
        mock_nc.request = AsyncMock(return_value=self._ok_reply())
        client = NatsTtsClient(nc=mock_nc)
        _inject_fresh_worker(
            client, "tts-heavy", vram_used_mb=12000, vram_total_mb=16384
        )
        _inject_fresh_worker(
            client, "tts-light", vram_used_mb=4800, vram_total_mb=16384
        )
        await client.synthesize("hi")
        subject = mock_nc.request.call_args.args[0]
        assert subject == "lyra.voice.tts.request.tts-light"

    @pytest.mark.asyncio
    async def test_active_requests_dominate_vram(self) -> None:
        """A busy worker (active_requests>0) loses to an idle higher-VRAM worker."""
        mock_nc = AsyncMock()
        mock_nc.request = AsyncMock(return_value=self._ok_reply())
        client = NatsTtsClient(nc=mock_nc)
        _inject_fresh_worker(
            client,
            "tts-busy",
            vram_used_mb=2000,
            vram_total_mb=16384,
            active_requests=2,
        )
        _inject_fresh_worker(
            client,
            "tts-idle-but-fuller",
            vram_used_mb=8000,
            vram_total_mb=16384,
            active_requests=0,
        )
        await client.synthesize("hi")
        subject = mock_nc.request.call_args.args[0]
        assert subject == "lyra.voice.tts.request.tts-idle-but-fuller"

    @pytest.mark.asyncio
    async def test_empty_registry_raises_without_request(self) -> None:
        """Empty registry → immediate TtsUnavailableError, no NATS request attempted."""
        mock_nc = AsyncMock()
        client = NatsTtsClient(nc=mock_nc)
        with pytest.raises(TtsUnavailableError, match="no live worker"):
            await client.synthesize("hi")
        mock_nc.request.assert_not_called()


class TestWalkRegistry:
    """Tests for _walk_registry method (replaces _send/_fallback in #813)."""

    @staticmethod
    def _ok_reply() -> MagicMock:
        payload = json.dumps(
            {
                "contract_version": "1",
                "trace_id": "tst-trace",
                "issued_at": "2026-04-19T00:00:00+00:00",
                "ok": True,
                "request_id": "r-ok",
                "audio_b64": base64.b64encode(b"fake").decode(),
                "mime_type": "audio/ogg",
                "duration_ms": 1000,
            }
        ).encode()
        reply = MagicMock()
        reply.data = payload
        return reply

    @pytest.mark.asyncio
    async def test_single_worker_success(self) -> None:
        """One worker, successful reply → returns response immediately."""
        # Arrange
        mock_nc = AsyncMock()
        mock_nc.request = AsyncMock(return_value=self._ok_reply())
        client = NatsTtsClient(nc=mock_nc)
        _inject_fresh_worker(client, "w1")
        payload = b'{"text":"hi"}'

        # Act
        result = await client._walk_registry(payload)

        # Assert
        assert result.ok is True
        assert mock_nc.request.await_count == 1
        subject = mock_nc.request.call_args.args[0]
        assert subject == "lyra.voice.tts.request.w1"

    @pytest.mark.asyncio
    async def test_timeout_walks_to_second(self) -> None:
        """W1 times out → mark_stale called → W2 succeeds."""
        # Arrange
        mock_nc = AsyncMock()
        call_order: list[str] = []

        async def request_mock(subject: str, payload: bytes, timeout: float):
            call_order.append(subject)
            if "w1" in subject:
                raise TimeoutError
            return self._ok_reply()

        mock_nc.request = AsyncMock(side_effect=request_mock)
        client = NatsTtsClient(nc=mock_nc)
        _inject_fresh_worker(client, "w1")
        _inject_fresh_worker(client, "w2")
        payload = b'{"text":"hi"}'

        # Act
        result = await client._walk_registry(payload)

        # Assert
        assert result.ok is True
        assert call_order == [
            "lyra.voice.tts.request.w1",
            "lyra.voice.tts.request.w2",
        ]
        # mark_stale should have been called on w1
        assert client._registry._workers["w1"].last_heartbeat == 0.0

    @pytest.mark.asyncio
    async def test_no_responders_walks_to_second(self) -> None:
        """W1 raises NoRespondersError → mark_stale called → W2 succeeds."""
        # Arrange
        from nats.errors import NoRespondersError

        mock_nc = AsyncMock()
        call_order: list[str] = []

        async def request_mock(subject: str, payload: bytes, timeout: float):
            call_order.append(subject)
            if "w1" in subject:
                raise NoRespondersError()
            return self._ok_reply()

        mock_nc.request = AsyncMock(side_effect=request_mock)
        client = NatsTtsClient(nc=mock_nc)
        _inject_fresh_worker(client, "w1")
        _inject_fresh_worker(client, "w2")
        payload = b'{"text":"hi"}'

        # Act
        result = await client._walk_registry(payload)

        # Assert
        assert result.ok is True
        assert "w1" in call_order[0]
        assert "w2" in call_order[1]
        assert client._registry._workers["w1"].last_heartbeat == 0.0

    @pytest.mark.asyncio
    async def test_all_fail_raises_unavailable(self) -> None:
        """All workers fail → raises TtsUnavailableError chained from last exception."""
        # Arrange
        mock_nc = AsyncMock()
        mock_nc.request = AsyncMock(side_effect=TimeoutError())
        client = NatsTtsClient(nc=mock_nc)
        _inject_fresh_worker(client, "w1")
        _inject_fresh_worker(client, "w2")
        payload = b'{"text":"hi"}'

        # Act / Assert
        with pytest.raises(
            TtsUnavailableError, match="all workers unresponsive"
        ) as exc_info:
            await client._walk_registry(payload)

        assert isinstance(exc_info.value.__cause__, TimeoutError)
        assert mock_nc.request.await_count == 2

    @pytest.mark.asyncio
    async def test_empty_registry_raises_immediately(self) -> None:
        """ordered_by_score() returns [] → raises TtsUnavailableError immediately."""
        # Arrange
        mock_nc = AsyncMock()
        client = NatsTtsClient(nc=mock_nc)
        # No workers injected
        payload = b'{"text":"hi"}'

        # Act / Assert
        with pytest.raises(TtsUnavailableError, match="no live worker"):
            await client._walk_registry(payload)

        mock_nc.request.assert_not_called()

    @pytest.mark.asyncio
    async def test_circuit_breaker_once_per_exhaustion(self) -> None:
        """record_failure() called once per walk exhaustion, not per candidate."""
        # Arrange
        mock_nc = AsyncMock()
        mock_nc.request = AsyncMock(side_effect=TimeoutError())
        client = NatsTtsClient(nc=mock_nc)
        _inject_fresh_worker(client, "w1")
        _inject_fresh_worker(client, "w2")
        payload = b'{"text":"hi"}'

        # Act
        with pytest.raises(TtsUnavailableError):
            await client._walk_registry(payload)

        # Assert — CB failure recorded ONCE (after exhausting all workers)
        assert client._cb._failures == 1

    @pytest.mark.asyncio
    async def test_logs_last_error_type(self, caplog: pytest.LogCaptureFixture) -> None:
        """WARNING log includes type(last_exc).__name__."""
        # Arrange
        import logging

        mock_nc = AsyncMock()
        mock_nc.request = AsyncMock(side_effect=TimeoutError())
        client = NatsTtsClient(nc=mock_nc)
        _inject_fresh_worker(client, "w1")
        payload = b'{"text":"hi"}'

        # Act
        with caplog.at_level(logging.WARNING):
            with pytest.raises(TtsUnavailableError):
                await client._walk_registry(payload)

        # Assert
        assert "TimeoutError" in caplog.text


class TestMalformedReply:
    """Pydantic ValidationError on reply MUST surface as TtsUnavailableError."""

    @pytest.mark.asyncio
    async def test_malformed_reply_raises_domain_error(self) -> None:
        """ok=True without duration_ms → TtsResponse invariant fails →
        client must translate into TtsUnavailableError and record a
        circuit-breaker failure (receive-path anti-drift guard).
        """
        mock_nc = AsyncMock()
        # Reply is ok=True but missing duration_ms — violates
        # TtsResponse._enforce_success_invariant (see contracts spec #763
        # drift item #1).
        bad_payload = json.dumps(
            {
                "contract_version": "1",
                "trace_id": "tst-trace",
                "issued_at": "2026-04-19T00:00:00+00:00",
                "ok": True,
                "request_id": "r-bad",
                "audio_b64": base64.b64encode(b"audio").decode(),
                "mime_type": "audio/ogg",
                # duration_ms deliberately omitted
            }
        ).encode()
        fake_reply = MagicMock()
        fake_reply.data = bad_payload
        mock_nc.request = AsyncMock(return_value=fake_reply)

        client = NatsTtsClient(nc=mock_nc)
        _inject_fresh_worker(client)
        initial_failures = client._cb._failures

        with pytest.raises(TtsUnavailableError, match="schema") as exc_info:
            await client.synthesize("hello")

        # Pin the cause chain to the _parse_reply error-boundary so a future
        # regression where ok=False handling accidentally produces a
        # "schema"-flavored message cannot silently pass this test.
        from pydantic import ValidationError

        assert isinstance(exc_info.value.__cause__, ValidationError)
        assert client._cb._failures == initial_failures + 1

    @pytest.mark.asyncio
    async def test_malformed_json_raises_domain_error(self) -> None:
        """Malformed JSON bytes (not just invariant violations) must also
        surface as TtsUnavailableError + CB failure — `_parse_reply` catches
        every pydantic.ValidationError, including JSON-parse errors."""
        mock_nc = AsyncMock()
        fake_reply = MagicMock()
        fake_reply.data = b"not json {"
        mock_nc.request = AsyncMock(return_value=fake_reply)

        client = NatsTtsClient(nc=mock_nc)
        _inject_fresh_worker(client)
        initial_failures = client._cb._failures

        with pytest.raises(TtsUnavailableError, match="schema") as exc_info:
            await client.synthesize("hello")

        from pydantic import ValidationError

        assert isinstance(exc_info.value.__cause__, ValidationError)
        assert client._cb._failures == initial_failures + 1
