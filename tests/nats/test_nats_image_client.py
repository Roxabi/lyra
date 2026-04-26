"""Tests for NatsImageClient — RED phase (T4).

The module under test (lyra.nats.nats_image_client) does not exist yet.
Imports intentionally fail with ModuleNotFoundError; T5 makes them pass.
"""

from __future__ import annotations

import base64
import json
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.nats.nats_image_client import (
    ImageUnavailableError,
    NatsImageClient,
)
from roxabi_contracts.image import ImageResponse

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _ok_reply_b64(request_id: str = "r1") -> bytes:
    """Return a valid ImageResponse JSON payload with ok=True and image_b64 set."""
    payload = {
        "contract_version": "1",
        "trace_id": "tst-trace",
        "issued_at": "2026-04-19T00:00:00+00:00",
        "ok": True,
        "request_id": request_id,
        "image_b64": base64.b64encode(b"fake-image-bytes").decode(),
        "mime_type": "image/png",
        "width": 512,
        "height": 512,
        "engine": "flux2-klein",
        "seed_used": 42,
    }
    return json.dumps(payload).encode()


def _heartbeat_bytes(
    worker_id: str = "img-1",
    active: int = 0,
    vram_used: int | None = None,
) -> bytes:
    """Return a heartbeat JSON payload; omit vram fields when vram_used is None."""
    payload: dict = {
        "contract_version": "1",
        "trace_id": "t",
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "worker_id": worker_id,
        "service": "image",
        "host": "test-host",
        "subject": "lyra.image.generate.request",
        "queue_group": "image_workers",
        "ts": time.time(),
        "engine_loaded": "flux2-klein",
        "active_requests": active,
    }
    if vram_used is not None:
        payload["vram_used_mb"] = vram_used
        payload["vram_total_mb"] = 16384
    # vram_used_mb / vram_total_mb deliberately absent when vram_used is None
    return json.dumps(payload).encode()


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------


class TestNatsImageClient:
    @pytest.mark.asyncio
    async def test_happy_path_round_trip(self) -> None:
        # Arrange — registry has a live worker; reply is a valid ImageResponse
        mock_nc = AsyncMock()
        fake_reply = MagicMock()
        fake_reply.data = _ok_reply_b64("r1")
        mock_nc.request = AsyncMock(return_value=fake_reply)
        client = NatsImageClient(nc=mock_nc)
        # Seed registry with a live worker
        client._registry.record_heartbeat({"worker_id": "img-1", "active_requests": 0})
        # Act
        result = await client.generate(prompt="test", engine="flux2-klein")
        # Assert
        assert isinstance(result, ImageResponse)
        assert result.ok is True
        assert result.image_b64 is not None
        assert len(base64.b64decode(result.image_b64)) > 0
        # record_success() was called — CB is neither failed nor open.
        # Both fields are zero on a happy-path round-trip. Prior assertion
        # only proved absence-of-failure; this pair proves success path ran.
        assert client._cb._failures == 0
        assert client._cb._open_until == 0.0

    @pytest.mark.asyncio
    async def test_reply_schema_failure_raises_domain_error(self) -> None:
        # Arrange — reply missing required `ok` field
        mock_nc = AsyncMock()
        bad_payload = json.dumps(
            {
                "contract_version": "1",
                "trace_id": "tst-trace",
                "issued_at": "2026-04-19T00:00:00+00:00",
                # `ok` deliberately omitted — schema violation
                "request_id": "r-bad",
                "image_b64": base64.b64encode(b"img").decode(),
            }
        ).encode()
        fake_reply = MagicMock()
        fake_reply.data = bad_payload
        mock_nc.request = AsyncMock(return_value=fake_reply)
        client = NatsImageClient(nc=mock_nc)
        client._registry.record_heartbeat({"worker_id": "img-1", "active_requests": 0})
        initial_failures = client._cb._failures
        # Act / Assert
        with pytest.raises(ImageUnavailableError, match="(?i)schema"):
            await client.generate(prompt="test", engine="flux2-klein")
        assert client._cb._failures == initial_failures + 1

    @pytest.mark.asyncio
    async def test_timeout_raises_domain_error(self) -> None:
        # Arrange — nc.request raises TimeoutError
        mock_nc = AsyncMock()
        mock_nc.request = AsyncMock(side_effect=TimeoutError())
        client = NatsImageClient(nc=mock_nc)
        client._registry.record_heartbeat({"worker_id": "img-1", "active_requests": 0})
        initial_failures = client._cb._failures
        # Act / Assert
        with pytest.raises(ImageUnavailableError, match="(?i)timeout"):
            await client.generate(prompt="test", engine="flux2-klein")
        assert client._cb._failures == initial_failures + 1

    @pytest.mark.asyncio
    async def test_request_max_payload_raises_domain_error(self) -> None:
        # The satellite downgrades oversized replies to file_path, so only outbound
        # requests over 1 MB surface this — this case covers request-direction only.

        # Arrange — nc.request raises an exception whose str() contains "max_payload"
        mock_nc = AsyncMock()
        mock_nc.request = AsyncMock(
            side_effect=Exception("NATS: max_payload exceeded (1048576 bytes)")
        )
        client = NatsImageClient(nc=mock_nc)
        client._registry.record_heartbeat({"worker_id": "img-1", "active_requests": 0})
        initial_failures = client._cb._failures
        # Act / Assert
        with pytest.raises(ImageUnavailableError, match="payload too large"):
            await client.generate(prompt="test", engine="flux2-klein")
        assert client._cb._failures == initial_failures + 1

    @pytest.mark.asyncio
    async def test_heartbeat_updates_registry(self) -> None:
        # Arrange — heartbeat payload without vram fields (optional-fields path)
        mock_nc = AsyncMock()
        client = NatsImageClient(nc=mock_nc)
        msg_mock = MagicMock()
        # vram_used_mb and vram_total_mb are absent to exercise the optional path
        msg_mock.data = _heartbeat_bytes(worker_id="img-1", active=0, vram_used=None)
        # Act
        await client._on_heartbeat(msg_mock)
        # Assert — worker is now reachable via the registry
        entry = client._registry.pick_least_loaded()
        assert entry is not None
        assert entry.worker_id == "img-1"
        # Absent vram fields must not prevent registry population
        assert entry.vram_used_mb == 0
        assert entry.vram_total_mb == 0

    @pytest.mark.asyncio
    async def test_stale_registry_raises_no_worker(self) -> None:
        # Arrange — empty registry (no workers ever registered)
        mock_nc = AsyncMock()
        mock_nc.request = AsyncMock()
        client = NatsImageClient(nc=mock_nc)
        # Act / Assert
        with pytest.raises(ImageUnavailableError, match="no live worker"):
            await client.generate(prompt="test", engine="flux2-klein")
        # nc.request must never have been awaited
        mock_nc.request.assert_not_called()

    @pytest.mark.asyncio
    async def test_open_circuit_raises_domain_error(self) -> None:
        # Arrange — live worker registered; trip the CB so is_open() returns True
        mock_nc = AsyncMock()
        mock_nc.request = AsyncMock()
        client = NatsImageClient(nc=mock_nc)
        client._registry.record_heartbeat({"worker_id": "img-1", "active_requests": 0})
        # Force CB open without going through real failures; mirrors the
        # internal shape of NatsCircuitBreaker (see packages/roxabi-nats).
        client._cb._open_until = time.monotonic() + 100.0
        # Act / Assert
        with pytest.raises(ImageUnavailableError, match="circuit open"):
            await client.generate(prompt="test", engine="flux2-klein")
        # nc.request must never have been awaited — early-exit on is_open()
        mock_nc.request.assert_not_called()

    @pytest.mark.asyncio
    async def test_ok_false_reply_propagates_error_field(self) -> None:
        # Arrange — valid-schema reply with ok=false + a specific error token
        mock_nc = AsyncMock()
        err_payload = json.dumps(
            {
                "contract_version": "1",
                "trace_id": "tst-trace",
                "issued_at": "2026-04-19T00:00:00+00:00",
                "request_id": "r-err",
                "ok": False,
                "error": "insufficient_resources",
                "error_detail": "Not enough VRAM to load engine",
            }
        ).encode()
        fake_reply = MagicMock()
        fake_reply.data = err_payload
        mock_nc.request = AsyncMock(return_value=fake_reply)
        client = NatsImageClient(nc=mock_nc)
        client._registry.record_heartbeat({"worker_id": "img-1", "active_requests": 0})
        initial_failures = client._cb._failures
        # Act / Assert — the error token must propagate into the exception
        # message so logs/metrics on the hub side can disambiguate error codes.
        with pytest.raises(ImageUnavailableError, match="insufficient_resources"):
            await client.generate(prompt="test", engine="flux2-klein")
        assert client._cb._failures == initial_failures + 1

    @pytest.mark.asyncio
    async def test_generic_exception_raises_adapter_unreachable(self) -> None:
        # Arrange — nc.request raises a generic Exception (not timeout, not
        # max_payload). Exercises the fallback branch in _raise_nats_failure.
        mock_nc = AsyncMock()
        mock_nc.request = AsyncMock(side_effect=Exception("connection refused"))
        client = NatsImageClient(nc=mock_nc)
        client._registry.record_heartbeat({"worker_id": "img-1", "active_requests": 0})
        initial_failures = client._cb._failures
        # Act / Assert
        with pytest.raises(ImageUnavailableError, match="adapter unreachable"):
            await client.generate(prompt="test", engine="flux2-klein")
        assert client._cb._failures == initial_failures + 1

    @pytest.mark.asyncio
    async def test_stop_calls_unsubscribe(self) -> None:
        """stop() calls unsubscribe() on the active subscription."""
        mock_nc = AsyncMock()
        mock_sub = AsyncMock()
        mock_nc.subscribe = AsyncMock(return_value=mock_sub)
        client = NatsImageClient(nc=mock_nc)
        await client.start()
        await client.stop()
        mock_sub.unsubscribe.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_sets_hb_sub_to_none(self) -> None:
        """After stop(), _hb_sub is None."""
        mock_nc = AsyncMock()
        mock_sub = AsyncMock()
        mock_nc.subscribe = AsyncMock(return_value=mock_sub)
        client = NatsImageClient(nc=mock_nc)
        await client.start()
        assert client._hb_sub is not None
        await client.stop()
        assert client._hb_sub is None

    @pytest.mark.asyncio
    async def test_stop_is_idempotent(self) -> None:
        """stop() called twice does not raise and only unsubscribes once."""
        mock_nc = AsyncMock()
        mock_sub = AsyncMock()
        mock_nc.subscribe = AsyncMock(return_value=mock_sub)
        client = NatsImageClient(nc=mock_nc)
        await client.start()
        await client.stop()
        await client.stop()
        mock_sub.unsubscribe.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_without_start_is_safe(self) -> None:
        """stop() before start() (hb_sub is None) does not raise."""
        mock_nc = AsyncMock()
        client = NatsImageClient(nc=mock_nc)
        await client.stop()  # must not raise
