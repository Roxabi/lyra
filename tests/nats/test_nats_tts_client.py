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


class TestCircuitBreaker:
    @pytest.mark.asyncio
    async def test_cb_open_blocks_synthesize(self) -> None:
        # Arrange — circuit manually forced open
        mock_nc = AsyncMock()
        client = NatsTtsClient(nc=mock_nc)
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
        # Act
        with pytest.raises(TtsUnavailableError):
            await client.synthesize("hello")
        # Assert
        assert client._cb._failures == 1

    @pytest.mark.asyncio
    async def test_failure_records_on_max_payload(self) -> None:
        # Arrange
        mock_nc = AsyncMock()
        mock_nc.request = AsyncMock(
            side_effect=Exception("NATS: max_payload exceeded")
        )
        client = NatsTtsClient(nc=mock_nc)
        # Act
        with pytest.raises(TtsUnavailableError, match="payload too large"):
            await client.synthesize("hello")
        # Assert
        assert client._cb._failures == 1

    @pytest.mark.asyncio
    async def test_ok_false_raises_unavailable(self) -> None:
        # Arrange
        mock_nc = AsyncMock()
        error_payload = json.dumps({"ok": False}).encode()
        fake_reply = MagicMock()
        fake_reply.data = error_payload
        mock_nc.request = AsyncMock(return_value=fake_reply)
        client = NatsTtsClient(nc=mock_nc)
        # Act / Assert
        with pytest.raises(TtsUnavailableError, match="synthesis failed"):
            await client.synthesize("hello")
        assert client._cb._failures == 1

    @pytest.mark.asyncio
    async def test_agent_tts_fields_forwarded_in_request(self) -> None:
        # Arrange — agent_tts with engine + speed set
        mock_nc = AsyncMock()
        success_payload = json.dumps({
            "ok": True,
            "audio_b64": base64.b64encode(b"fake").decode(),
            "mime_type": "audio/ogg",
        }).encode()
        fake_reply = MagicMock()
        fake_reply.data = success_payload
        mock_nc.request = AsyncMock(return_value=fake_reply)
        client = NatsTtsClient(nc=mock_nc)

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
        # All unset fields (None) must be absent from the NATS payload
        none_fields = [f for f in _TTS_CONFIG_FIELDS if f not in ("engine", "speed")]
        assert all(f not in request_dict for f in none_fields)

    @pytest.mark.asyncio
    async def test_success_clears_failures(self) -> None:
        # Arrange — pre-inject 2 failures
        mock_nc = AsyncMock()
        success_payload = json.dumps({
            "ok": True,
            "audio_b64": base64.b64encode(b"fake").decode(),
            "mime_type": "audio/ogg",
        }).encode()
        fake_reply = MagicMock()
        fake_reply.data = success_payload
        mock_nc.request = AsyncMock(return_value=fake_reply)
        client = NatsTtsClient(nc=mock_nc)
        client._cb._failures = 2
        # Act
        result = await client.synthesize("hello")
        # Assert
        assert result.audio_bytes == b"fake"
        assert client._cb._failures == 0
