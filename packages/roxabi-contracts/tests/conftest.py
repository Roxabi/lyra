"""Pytest fixtures for roxabi-contracts tests.

Ports the `nats_server_url` session-scoped fixture from
`packages/roxabi-nats/tests/conftest.py` so integration tests for
`voice.testing` can subscribe/publish against a real loopback NATS
without cross-package dependency.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Generator
from pathlib import Path

import pytest

# Tests in this package deliberately have no ``__init__.py`` (aligns with
# ``packages/roxabi-nats/tests`` — a package-level ``__init__.py`` makes
# two conftest modules collide under ``tests.conftest``). Add the test
# directory to sys.path so sibling helpers like ``_markers`` resolve both
# here (conftest load, before pytest injects the path) and in test modules.
_TEST_DIR = str(Path(__file__).resolve().parent)
if _TEST_DIR not in sys.path:
    sys.path.insert(0, _TEST_DIR)

from _markers import requires_nats_server as requires_nats_server  # noqa: E402

_nats_server_available = shutil.which("nats-server") is not None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def nats_server_url() -> Generator[str, None, None]:
    if not _nats_server_available:
        pytest.skip("nats-server not found in PATH")
    port = _free_port()
    url = f"nats://127.0.0.1:{port}"
    proc = subprocess.Popen(
        ["nats-server", "-p", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
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
