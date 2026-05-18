"""Tests for NatsWorkerClientBase — shared heartbeat lifecycle."""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock

import pytest

from lyra.nats._worker_client_base import NatsWorkerClientBase
from roxabi_contracts.llm import validate_worker_id

# ---------------------------------------------------------------------------
# Fake subclass (minimal concrete implementation for tests)
# ---------------------------------------------------------------------------


class FakeWorkerClient(NatsWorkerClientBase):
    HB_SUBJECT = "test.heartbeat"
    LOG_PREFIX = "fake:"
    VALIDATE_WORKER_ID = staticmethod(validate_worker_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_HB = {
    "worker_id": "worker-1",
    "vram_used_mb": 100,
    "vram_total_mb": 1000,
    "active_requests": 0,
}


def _make_msg(payload: dict | str | bytes) -> MagicMock:
    msg = MagicMock()
    if isinstance(payload, bytes):
        msg.data = payload
    elif isinstance(payload, str):
        msg.data = payload.encode()
    else:
        msg.data = json.dumps(payload).encode()
    return msg


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client() -> FakeWorkerClient:
    nc = MagicMock()
    nc.is_connected = True
    return FakeWorkerClient(nc)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestNatsWorkerClientBase:
    @pytest.mark.asyncio
    async def test_valid_heartbeat_populates_both_sinks(
        self, client: FakeWorkerClient
    ) -> None:
        """A valid heartbeat feeds both WorkerRegistry and _worker_freshness."""
        # Arrange
        before = time.monotonic()
        msg = _make_msg(_VALID_HB)

        # Act
        await client._on_heartbeat(msg)

        # Assert — registry sink
        assert client._registry.any_alive() is True
        # Assert — parent freshness sink
        assert "worker-1" in client._worker_freshness
        ts = client._worker_freshness["worker-1"]
        assert ts >= before
        assert ts <= time.monotonic()
        # Assert — public helper
        assert client.any_alive() is True

    @pytest.mark.asyncio
    async def test_unsafe_worker_id_rejected_via_validate_hook(
        self, client: FakeWorkerClient
    ) -> None:
        """VALIDATE_WORKER_ID raises ValueError for IDs with wildcards/spaces."""
        unsafe_ids = ["worker.*", "worker>", "worker.id", "worker id", "*"]
        for unsafe_id in unsafe_ids:
            payload = {**_VALID_HB, "worker_id": unsafe_id}
            msg = _make_msg(payload)
            # Act — must not raise
            await client._on_heartbeat(msg)
        # Assert — registry stays empty, freshness stays empty
        assert client._registry.any_alive() is False
        assert client._worker_freshness == {}

    @pytest.mark.asyncio
    async def test_missing_worker_id_dropped(self, client: FakeWorkerClient) -> None:
        """Heartbeat with no worker_id key is silently dropped."""
        payload = {"vram_used_mb": 100, "vram_total_mb": 1000, "active_requests": 0}
        msg = _make_msg(payload)

        await client._on_heartbeat(msg)

        assert client._registry.any_alive() is False
        assert client._worker_freshness == {}

    @pytest.mark.asyncio
    async def test_empty_worker_id_dropped(self, client: FakeWorkerClient) -> None:
        """Heartbeat with worker_id="" is silently dropped."""
        payload = {**_VALID_HB, "worker_id": ""}
        msg = _make_msg(payload)

        await client._on_heartbeat(msg)

        assert client._registry.any_alive() is False
        assert client._worker_freshness == {}

    @pytest.mark.asyncio
    async def test_non_string_worker_id_dropped(self, client: FakeWorkerClient) -> None:
        """Heartbeat with worker_id=42 (non-string) is silently dropped."""
        payload = {**_VALID_HB, "worker_id": 42}
        msg = _make_msg(payload)

        await client._on_heartbeat(msg)

        assert client._registry.any_alive() is False
        assert client._worker_freshness == {}

    @pytest.mark.asyncio
    async def test_malformed_json_silently_dropped(
        self, client: FakeWorkerClient
    ) -> None:
        """Malformed JSON bytes produce no exception, registry/freshness stay empty."""
        msg = _make_msg(b"not-json{")

        # Act — must not raise
        await client._on_heartbeat(msg)

        # Assert
        assert client._registry.any_alive() is False
        assert client._worker_freshness == {}

    @pytest.mark.asyncio
    async def test_liveness_parity_fresh_heartbeat(
        self, client: FakeWorkerClient
    ) -> None:
        """_any_worker_alive() and any_alive() agree after a fresh heartbeat."""
        msg = _make_msg(_VALID_HB)
        await client._on_heartbeat(msg)

        assert client._any_worker_alive() is True
        assert client.any_alive() is True
        # Both must return the same value
        assert client._any_worker_alive() == client.any_alive()

    @pytest.mark.asyncio
    async def test_liveness_parity_no_heartbeat(self, client: FakeWorkerClient) -> None:
        """_any_worker_alive() and any_alive() both return False with no heartbeats."""
        assert client._any_worker_alive() is False
        assert client.any_alive() is False
        assert client._any_worker_alive() == client.any_alive()

    @pytest.mark.asyncio
    async def test_liveness_parity_stale_heartbeat(
        self, client: FakeWorkerClient
    ) -> None:
        """_any_worker_alive() and any_alive() both report False for expired workers.

        WorkerRegistry uses DEFAULT_HB_TTL=15s; NatsDriverBase uses HB_TTL=30s.
        We manipulate both timestamps directly so the test is deterministic and fast.
        """
        msg = _make_msg(_VALID_HB)
        await client._on_heartbeat(msg)

        # Simulate expiry: push timestamps far into the past
        stale_ts = time.monotonic() - 1000.0
        client._worker_freshness["worker-1"] = stale_ts
        if "worker-1" in client._registry._workers:
            client._registry._workers["worker-1"].last_heartbeat = stale_ts

        assert client._any_worker_alive() is False
        assert client.any_alive() is False
        assert client._any_worker_alive() == client.any_alive()

    @pytest.mark.asyncio
    async def test_any_alive_delegates_to_registry(
        self, client: FakeWorkerClient
    ) -> None:
        """any_alive() is a pure delegation to WorkerRegistry.any_alive()."""
        # Initially both False
        assert client.any_alive() is False
        # Seed registry directly (bypass _on_heartbeat)
        client._registry.record_heartbeat(_VALID_HB)
        assert client.any_alive() is True
