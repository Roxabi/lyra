"""Tests for decode_stream_events health-check abort (issue #1154)."""

from __future__ import annotations

import asyncio

import pytest

from lyra.adapters.nats.nats_stream_decoder import decode_stream_events
from lyra.core.exceptions import HubUnavailableError, StreamChunkTimeout


async def _drain(stream_id: str, q: asyncio.Queue, **kwargs) -> list:
    results = []
    async for event in decode_stream_events(stream_id, q, **kwargs):
        results.append(event)
    return results


class TestDecodeStreamEventsHealthCheck:
    """decode_stream_events aborts on hub health check failure."""

    @pytest.mark.asyncio
    async def test_hub_unavailable_raises(self) -> None:
        """HubUnavailableError raised when health_check_fn returns False."""
        q: asyncio.Queue[dict] = asyncio.Queue()

        async def _unhealthy() -> bool:
            return False

        with pytest.raises(HubUnavailableError):
            # Patch the poll interval to make test fast
            import lyra.adapters.nats.nats_stream_decoder as mod

            original = mod._LIVENESS_POLL_SECONDS
            mod._LIVENESS_POLL_SECONDS = 0.05
            try:
                await _drain("test-stream-1", q, health_check_fn=_unhealthy)
            finally:
                mod._LIVENESS_POLL_SECONDS = original

    @pytest.mark.asyncio
    async def test_healthy_check_does_not_abort(self) -> None:
        """Healthy hub check does not abort; timeout still applies."""
        q: asyncio.Queue[dict] = asyncio.Queue()
        call_count = 0

        async def _healthy() -> bool:
            nonlocal call_count
            call_count += 1
            return True

        import lyra.adapters.nats.nats_stream_decoder as mod

        original_poll = mod._LIVENESS_POLL_SECONDS
        original_timeout = mod._CHUNK_TIMEOUT_SECONDS
        mod._LIVENESS_POLL_SECONDS = 0.05
        mod._CHUNK_TIMEOUT_SECONDS = 0.15  # short timeout for test
        try:
            with pytest.raises(StreamChunkTimeout):
                await _drain("test-stream-2", q, health_check_fn=_healthy)
        finally:
            mod._LIVENESS_POLL_SECONDS = original_poll
            mod._CHUNK_TIMEOUT_SECONDS = original_timeout

        assert call_count >= 1  # health_check_fn was called

    @pytest.mark.asyncio
    async def test_no_health_check_fn_timeout(self) -> None:
        """Without health_check_fn, StreamChunkTimeout is raised after timeout."""
        q: asyncio.Queue[dict] = asyncio.Queue()

        import lyra.adapters.nats.nats_stream_decoder as mod

        original_poll = mod._LIVENESS_POLL_SECONDS
        original_timeout = mod._CHUNK_TIMEOUT_SECONDS
        mod._LIVENESS_POLL_SECONDS = 0.05
        mod._CHUNK_TIMEOUT_SECONDS = 0.1
        try:
            with pytest.raises(StreamChunkTimeout):
                await _drain("test-stream-3", q)
        finally:
            mod._LIVENESS_POLL_SECONDS = original_poll
            mod._CHUNK_TIMEOUT_SECONDS = original_timeout
