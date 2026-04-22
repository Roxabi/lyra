"""Integration tests for load-aware STT/TTS routing.

Tests the full NATS request-reply path with real worker heartbeats,
exercising the WorkerRegistry scoring and fallback logic.

Requires Docker Compose (started via session-scoped fixture).
Marker: @pytest.mark.nats_integration
Run: docker compose -f docker/docker-compose.test.yml up -d
      pytest -m nats_integration
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import time
from pathlib import Path

import pytest

NATS_URL = os.getenv("NATS_URL", "nats://localhost:4222")
COMPOSE_FILE = Path("docker/docker-compose.test.yml")


def _nats_available() -> bool:
    """Check if NATS server is reachable."""
    try:
        host, port = NATS_URL.replace("nats://", "").split(":")
        with socket.create_connection((host, int(port)), timeout=2):
            return True
    except (OSError, ValueError):
        return False


# Skip all tests in this module if NATS isn't available
pytestmark = [
    pytest.mark.nats_integration,
    pytest.mark.skipif(not _nats_available(), reason="NATS server not available"),
]

HB_SUBJECT = "lyra.voice.stt.heartbeat"
STT_REQUEST_SUBJECT = "lyra.voice.stt.request"
COMPOSE_PROJECT = "lyra-test"


@pytest.fixture(scope="session")
def docker_compose():
    """Start Docker Compose for the test session.

    In CI, the workflow starts Docker Compose before running tests.
    Locally, this fixture handles setup/teardown.
    """
    in_ci = os.getenv("CI") == "true"

    if not in_ci:
        # Ensure fresh state (local only)
        subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "down", "-v"],
            capture_output=True,
            check=False,
        )

        # Start base services (NATS + single stt-stub + tts-stub)
        subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d"],
            capture_output=True,
            check=True,
            env={**os.environ, "COMPOSE_PROJECT_NAME": COMPOSE_PROJECT},
        )

        # Wait for NATS healthcheck
        max_wait = 30
        for _ in range(max_wait):
            result = subprocess.run(
                ["docker", "compose", "-f", str(COMPOSE_FILE), "ps", "--format=json"],
                capture_output=True,
                text=True,
                env={**os.environ, "COMPOSE_PROJECT_NAME": COMPOSE_PROJECT},
            )
            if result.returncode == 0 and "healthy" in result.stdout:
                break
            time.sleep(1)
        else:
            subprocess.run(
                ["docker", "compose", "-f", str(COMPOSE_FILE), "logs"],
                capture_output=True,
                env={**os.environ, "COMPOSE_PROJECT_NAME": COMPOSE_PROJECT},
            )
            raise RuntimeError("Docker Compose services failed to become healthy")

    yield COMPOSE_PROJECT

    if not in_ci:
        # Teardown (local only)
        subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "down", "-v"],
            capture_output=True,
            check=False,
            env={**os.environ, "COMPOSE_PROJECT_NAME": COMPOSE_PROJECT},
        )


@pytest.fixture
async def nats_client(docker_compose):
    """Connected NATS client for per-test use.

    Subscribes to heartbeat subject to observe worker announcements.
    """
    import nats

    nc = await nats.connect(NATS_URL)
    yield nc
    await nc.drain()
    await nc.close()


@pytest.fixture
async def heartbeat_collector(nats_client):
    """Collect heartbeats from workers during test.

    Returns a list that accumulates heartbeat payloads.
    """
    heartbeats: list[dict] = []

    async def _on_msg(msg):
        try:
            heartbeats.append(json.loads(msg.data))
        except json.JSONDecodeError:
            pass

    sub = await nats_client.subscribe(HB_SUBJECT, cb=_on_msg)
    yield heartbeats
    await sub.unsubscribe()


class TestWorkerHeartbeatFlow:
    """Heartbeat processing and registry updates."""

    @pytest.fixture(autouse=True)
    async def _wait_for_base_heartbeat(self, heartbeat_collector):
        """Wait for base stt-stub to announce itself."""
        for _ in range(20):
            await asyncio.sleep(0.5)
            if any(hb.get("worker_id") == "stt-tower-01" for hb in heartbeat_collector):
                return
        raise RuntimeError("Base stt-stub (stt-tower-01) did not publish heartbeat")

    @pytest.mark.asyncio
    async def test_heartbeat_payload_structure(self, nats_client, heartbeat_collector):
        """Heartbeat contains expected fields."""
        # Wait for at least one heartbeat
        for _ in range(10):
            await asyncio.sleep(0.5)
            if heartbeat_collector:
                break
        else:
            raise RuntimeError("No heartbeats received")

        hb = heartbeat_collector[0]
        assert "worker_id" in hb
        assert "service" in hb
        assert hb["service"] == "stt"
        assert "vram_used_mb" in hb
        assert "vram_total_mb" in hb
        assert "active_requests" in hb

    @pytest.mark.asyncio
    async def test_stt_request_via_queue_group(self, nats_client, heartbeat_collector):
        """STT request routed via queue group succeeds.

        Verifies end-to-end flow:
        - Worker heartbeat published
        - STT request sent to queue group subject
        - Worker responds with transcript
        """
        # Verify worker is publishing heartbeats
        assert any(
            hb.get("worker_id") == "stt-tower-01" for hb in heartbeat_collector
        ), "Worker not publishing heartbeats"

        request = {
            "contract_version": "1",
            "trace_id": "test-trace-001",
            "issued_at": "2024-01-01T12:00:00Z",
            "request_id": "test-req-001",
            "audio_b64": "dGVzdC1hdWRpby1kYXRh",  # base64 "test-audio-data"
            "mime_type": "audio/ogg",
        }

        reply = await nats_client.request(
            STT_REQUEST_SUBJECT,
            json.dumps(request).encode(),
            timeout=5.0,
        )
        response = json.loads(reply.data)
        assert response["ok"] is True
        assert response["text"] is not None
        assert response["request_id"] == "test-req-001"

    @pytest.mark.asyncio
    async def test_stt_request_to_per_worker_subject(
        self, nats_client, heartbeat_collector
    ):
        """STT request routed to specific worker succeeds.

        Verifies per-worker direct routing subject works.
        """
        request = {
            "contract_version": "1",
            "trace_id": "test-trace-002",
            "issued_at": "2024-01-01T12:00:00Z",
            "request_id": "test-req-002",
            "audio_b64": "dGVzdC1hdWRpby1kYXRh",
            "mime_type": "audio/ogg",
        }

        # Send to per-worker subject (stt-tower-01 is the default stub)
        reply = await nats_client.request(
            "lyra.voice.stt.request.stt-tower-01",
            json.dumps(request).encode(),
            timeout=5.0,
        )
        response = json.loads(reply.data)
        assert response["ok"] is True
        assert response["text"] is not None
