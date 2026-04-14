"""Tests for NatsTtsClient circuit breaker integration."""

from __future__ import annotations

import base64
import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.agent_config import AgentTTSConfig
from lyra.nats.nats_tts_client import _TTS_CONFIG_FIELDS, NatsTtsClient
from lyra.tts import TtsUnavailableError


def _inject_fresh_worker(client: NatsTtsClient) -> None:
    """Seed _worker_freshness with a fresh timestamp so freshness gate passes."""
    client._worker_freshness["test-worker"] = time.monotonic()


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
        error_payload = json.dumps({"contract_version": "1", "ok": False}).encode()
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
    async def test_agent_tts_fields_forwarded_in_request(self) -> None:
        # Arrange — agent_tts with engine + speed set
        mock_nc = AsyncMock()
        success_payload = json.dumps(
            {
                "contract_version": "1",
                "ok": True,
                "audio_b64": base64.b64encode(b"fake").decode(),
                "mime_type": "audio/ogg",
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
                "ok": True,
                "audio_b64": base64.b64encode(b"fake").decode(),
                "mime_type": "audio/ogg",
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
                "ok": True,
                "audio_b64": base64.b64encode(b"hi").decode(),
                "mime_type": "audio/ogg",
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
                "ok": True,
                "audio_b64": base64.b64encode(b"future").decode(),
                "mime_type": "audio/ogg",
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
        client._worker_freshness["worker-1"] = time.monotonic() - 20.0
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
                "ok": True,
                "audio_b64": base64.b64encode(b"audio").decode(),
                "mime_type": "audio/ogg",
            }
        ).encode()
        mock_nc.request = AsyncMock(return_value=mock_response)
        client = NatsTtsClient(nc=mock_nc)
        client._worker_freshness["worker-1"] = time.monotonic() - 5.0
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
                "ok": True,
                "audio_b64": base64.b64encode(b"audio").decode(),
                "mime_type": "audio/ogg",
            }
        ).encode()
        mock_nc.request = AsyncMock(return_value=mock_response)
        client = NatsTtsClient(nc=mock_nc)
        # First: stale
        client._worker_freshness["worker-1"] = time.monotonic() - 20.0
        with pytest.raises(TtsUnavailableError, match="no live worker"):
            await client.synthesize("hello")
        # Simulate fresh heartbeat arrives
        client._worker_freshness["worker-1"] = time.monotonic()
        result = await client.synthesize("hello")
        assert result.audio_bytes == b"audio"

    def test_any_worker_alive_true_within_ttl(self) -> None:
        """_any_worker_alive() returns True when a worker has a recent timestamp."""
        mock_nc = MagicMock()
        client = NatsTtsClient(nc=mock_nc)
        client._worker_freshness["worker-1"] = time.monotonic() - 5.0
        assert client._any_worker_alive() is True

    def test_any_worker_alive_false_when_stale(self) -> None:
        """_any_worker_alive() returns False when all workers are >15s stale."""
        mock_nc = MagicMock()
        client = NatsTtsClient(nc=mock_nc)
        client._worker_freshness["worker-1"] = time.monotonic() - 20.0
        assert client._any_worker_alive() is False

    def test_any_worker_alive_true_with_mixed_freshness(self) -> None:
        """_any_worker_alive() returns True when at least one worker is fresh."""
        mock_nc = MagicMock()
        client = NatsTtsClient(nc=mock_nc)
        client._worker_freshness["stale-worker"] = time.monotonic() - 20.0
        client._worker_freshness["fresh-worker"] = time.monotonic() - 5.0
        assert client._any_worker_alive() is True

    def test_stale_entries_pruned_in_any_worker_alive(self) -> None:
        """_any_worker_alive() evicts entries older than TTL*2."""
        mock_nc = MagicMock()
        client = NatsTtsClient(nc=mock_nc)
        client._worker_freshness["ancient"] = time.monotonic() - 35.0  # > 15*2
        client._worker_freshness["fresh"] = time.monotonic() - 5.0
        client._any_worker_alive()
        assert "ancient" not in client._worker_freshness
        assert "fresh" in client._worker_freshness
