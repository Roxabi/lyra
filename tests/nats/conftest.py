"""Shared fixtures for NatsBus unit tests.

Uses a real nats-server subprocess on an ephemeral port — no mocks.
nats-server binary must be in PATH (installed via ``make nats-install`` in
lyra-stack, or available as a system package). Tests that depend on the ``nc``
fixture are automatically skipped when nats-server is not found.
"""
from __future__ import annotations

import shutil
import socket
import subprocess
import time
from collections.abc import AsyncGenerator, Generator

import nats
import pytest

# Skip marker applied automatically to any test that depends on nats-server.
_nats_server_available = shutil.which("nats-server") is not None
requires_nats_server = pytest.mark.skipif(
    not _nats_server_available,
    reason=(
        "nats-server not found in PATH"
        " — install via 'make nats-install' in lyra-stack"
    ),
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def nats_server_url() -> Generator[str, None, None]:
    """Spawn a nats-server subprocess for the test session.

    Skipped automatically when nats-server is not in PATH.
    """
    if not _nats_server_available:
        pytest.skip("nats-server not found in PATH")
    port = _free_port()
    url = f"nats://127.0.0.1:{port}"
    proc = subprocess.Popen(
        ["nats-server", "-p", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Wait for server to be ready
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.05)
    else:
        proc.terminate()
        raise RuntimeError(f"nats-server did not start on port {port}")
    yield url
    proc.terminate()
    proc.wait()


@pytest.fixture()
async def nc(nats_server_url: str) -> AsyncGenerator[nats.NATS, None]:
    """Return a connected nats.NATS client, drained after each test."""
    conn = await nats.connect(nats_server_url)
    yield conn
    if conn.is_connected:
        await conn.drain()
