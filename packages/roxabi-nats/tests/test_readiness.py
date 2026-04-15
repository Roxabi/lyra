"""Tests for the NATS readiness probe (lyra.nats.readiness).

Covered behaviours:
- start_readiness_responder() replies to lyra.system.ready with a valid JSON payload
- wait_for_hub() returns True when a responder is running
- wait_for_hub() returns False and logs WARNING when no responder is present
- wait_for_hub() succeeds when the responder starts after the probe begins
  (concurrent-startup race simulation)
- wait_for_hub() returns False and logs on unexpected errors
- start_readiness_responder() handles buses=[] (sum of empty = 0)
"""

from __future__ import annotations

import asyncio
import json
import logging

import pytest
from nats.aio.client import Client as NATS

import nats
from roxabi_nats.readiness import (
    PROBE_INTERVAL_S,
    PROBE_TIMEOUT_S,
    READINESS_SUBJECT,
    start_readiness_responder,
    wait_for_hub,
)

from .conftest import requires_nats_server

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeBus:
    """Minimal stand-in for NatsBus — exposes only subscription_count."""

    def __init__(self, count: int = 2) -> None:
        self._count = count

    @property
    def subscription_count(self) -> int:
        return self._count


# ---------------------------------------------------------------------------
# TestReadinessConstants — module-level constant sanity
# ---------------------------------------------------------------------------


class TestReadinessConstants:
    def test_readiness_subject_value(self) -> None:
        """READINESS_SUBJECT must equal 'lyra.system.ready'."""
        assert READINESS_SUBJECT == "lyra.system.ready"

    def test_probe_interval_is_positive(self) -> None:
        """PROBE_INTERVAL_S must be a positive float."""
        assert isinstance(PROBE_INTERVAL_S, float)
        assert PROBE_INTERVAL_S > 0

    def test_probe_timeout_is_positive(self) -> None:
        """PROBE_TIMEOUT_S must be a positive float."""
        assert isinstance(PROBE_TIMEOUT_S, float)
        assert PROBE_TIMEOUT_S > 0

    def test_probe_timeout_greater_than_interval(self) -> None:
        """PROBE_TIMEOUT_S must be larger than PROBE_INTERVAL_S."""
        assert PROBE_TIMEOUT_S > PROBE_INTERVAL_S


# ---------------------------------------------------------------------------
# TestReadinessReply — SC-1: responder answers wait_for_hub
# ---------------------------------------------------------------------------


@requires_nats_server
class TestReadinessReply:
    async def test_readiness_reply_returns_true(
        self, nc: NATS, nats_server_url: str
    ) -> None:
        """wait_for_hub returns True when a responder is active on another connection.

        Uses two NATS connections to mirror the real hub/adapter split:
        - nc  (from fixture) runs the responder (hub side)
        - nc2 (created here)  runs the probe  (adapter side)
        """
        # Arrange — start responder on hub connection
        buses = [FakeBus(count=2), FakeBus(count=3)]
        sub = await start_readiness_responder(nc, buses)

        nc2 = await nats.connect(nats_server_url)
        try:
            # Act
            result = await wait_for_hub(nc2, timeout=5.0)

            # Assert — probe succeeded
            assert result is True
        finally:
            await sub.unsubscribe()
            if nc2.is_connected:
                await nc2.drain()

    async def test_readiness_reply_payload_shape(
        self, nc: NATS, nats_server_url: str
    ) -> None:
        """Reply payload contains 'status', 'uptime_s', and 'buses' keys."""
        # Arrange — start responder; issue a raw NATS request to capture reply
        buses = [FakeBus(count=2)]
        sub = await start_readiness_responder(nc, buses)

        nc2 = await nats.connect(nats_server_url)
        try:
            # Act — send a request directly so we can inspect the raw reply
            msg = await nc2.request(READINESS_SUBJECT, b"", timeout=5.0)
            payload = json.loads(msg.data.decode())

            # Assert — required keys present
            assert "status" in payload, f"Missing 'status' key in: {payload}"
            assert "uptime_s" in payload, f"Missing 'uptime_s' key in: {payload}"
            assert "buses" in payload, f"Missing 'buses' key in: {payload}"

            # Assert — value types
            assert payload["status"] == "ready"
            assert isinstance(payload["uptime_s"], (int, float))
            assert payload["uptime_s"] >= 0
            assert isinstance(payload["buses"], int)
        finally:
            await sub.unsubscribe()
            if nc2.is_connected:
                await nc2.drain()

    async def test_readiness_buses_count_is_sum_of_subscription_counts(
        self, nc: NATS, nats_server_url: str
    ) -> None:
        """'buses' in reply equals sum of all NatsBus.subscription_count values."""
        # Arrange — two buses with known subscription counts
        buses = [FakeBus(count=3), FakeBus(count=5)]
        sub = await start_readiness_responder(nc, buses)

        nc2 = await nats.connect(nats_server_url)
        try:
            # Act
            msg = await nc2.request(READINESS_SUBJECT, b"", timeout=5.0)
            payload = json.loads(msg.data.decode())

            # Assert — sum is 3 + 5 = 8
            assert payload["buses"] == 8
        finally:
            await sub.unsubscribe()
            if nc2.is_connected:
                await nc2.drain()


# ---------------------------------------------------------------------------
# TestReadinessTimeout — SC-2: wait_for_hub gives up and logs WARNING
# ---------------------------------------------------------------------------


@requires_nats_server
class TestReadinessTimeout:
    async def test_wait_for_hub_returns_false_on_timeout(self, nc: NATS) -> None:
        """wait_for_hub returns False when no responder and timeout expires."""
        # Arrange — no responder subscribed; use a short timeout to keep the test fast

        # Act
        result = await wait_for_hub(nc, timeout=0.5)

        # Assert
        assert result is False

    async def test_wait_for_hub_logs_warning_on_timeout(
        self, nc: NATS, caplog: pytest.LogCaptureFixture
    ) -> None:
        """wait_for_hub emits a WARNING-level log entry when it times out."""
        # Arrange — no responder; capture WARNING+ logs from the readiness module
        with caplog.at_level(logging.WARNING, logger="roxabi_nats.readiness"):
            # Act
            result = await wait_for_hub(nc, timeout=0.5)

        # Assert — timed out
        assert result is False

        # Assert — at least one WARNING was logged
        warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warning_records, (
            "Expected at least one WARNING log from wait_for_hub on timeout; "
            f"captured records: {caplog.records}"
        )


# ---------------------------------------------------------------------------
# TestConcurrentStartup — SC-3: probe succeeds when hub starts after probe begins
# ---------------------------------------------------------------------------


@requires_nats_server
class TestConcurrentStartup:
    async def test_wait_for_hub_succeeds_when_responder_starts_after_probe(
        self, nc: NATS, nats_server_url: str
    ) -> None:
        """wait_for_hub waits and returns True when responder starts 200ms after probe.

        Simulates the real race: adapter calls wait_for_hub before hub is ready,
        then hub boots and starts the responder while the probe is still retrying.
        """
        # Arrange — second connection for the hub side
        hub_nc = await nats.connect(nats_server_url)

        sub = None
        try:
            # Act — launch probe as a concurrent task (adapter side)
            probe_task = asyncio.create_task(
                wait_for_hub(nc, timeout=5.0),
                name="readiness-probe",
            )

            # Simulate hub starting 200ms after the probe begins
            await asyncio.sleep(0.2)
            buses = [FakeBus(count=1)]
            sub = await start_readiness_responder(hub_nc, buses)

            # Wait for probe to resolve
            result = await asyncio.wait_for(probe_task, timeout=6.0)

            # Assert — probe eventually succeeded
            assert result is True, (
                "wait_for_hub should return True once the responder started, "
                "but it returned False"
            )
        finally:
            if sub is not None:
                await sub.unsubscribe()
            if hub_nc.is_connected:
                await hub_nc.drain()


# ---------------------------------------------------------------------------
# TestReadinessEdgeCases — empty buses list + unexpected errors
# ---------------------------------------------------------------------------


@requires_nats_server
class TestReadinessEmptyBuses:
    async def test_empty_buses_list_reports_zero(
        self, nc: NATS, nats_server_url: str
    ) -> None:
        """start_readiness_responder with buses=[] replies with buses=0.

        sum([]) is 0 — this exercises the default branch so a regression
        that raises on empty input is caught.
        """
        # Arrange — zero buses
        sub = await start_readiness_responder(nc, [])

        nc2 = await nats.connect(nats_server_url)
        try:
            # Act
            msg = await nc2.request(READINESS_SUBJECT, b"", timeout=5.0)
            payload = json.loads(msg.data.decode())

            # Assert
            assert payload["buses"] == 0
            assert payload["status"] == "ready"
        finally:
            await sub.unsubscribe()
            if nc2.is_connected:
                await nc2.drain()


class TestWaitForHubUnexpectedError:
    async def test_unexpected_error_is_logged_and_returns_false(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """wait_for_hub catches unexpected errors, logs them, and returns False.

        Injects a fake NATS client whose .request() raises RuntimeError on
        every call. Probe should exhaust the timeout, log via log.exception,
        and return False.
        """

        # Arrange — fake NATS client that raises unexpected errors
        class BrokenNats:
            async def request(
                self, subject: str, payload: bytes, timeout: float
            ) -> None:
                raise RuntimeError("synthetic transport fault")

        with caplog.at_level(logging.ERROR, logger="roxabi_nats.readiness"):
            # Act — short timeout so the test is fast
            result = await wait_for_hub(BrokenNats(), timeout=0.6)  # type: ignore[arg-type]

        # Assert — returned False (graceful degradation)
        assert result is False

        # Assert — log.exception emitted at ERROR level
        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert error_records, (
            "Expected at least one ERROR log from wait_for_hub on unexpected "
            f"error; captured records: {caplog.records}"
        )
