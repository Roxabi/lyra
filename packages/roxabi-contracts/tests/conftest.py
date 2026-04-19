"""Pytest fixtures for roxabi-contracts tests.

Ports the `nats_server_url` session-scoped fixture from
`packages/roxabi-nats/tests/conftest.py` so integration tests for
`voice.testing` can subscribe/publish against a real loopback NATS
without cross-package dependency.
"""

from __future__ import annotations

import importlib.util
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Generator
from pathlib import Path

import pytest


def _register_markers() -> None:
    """Register ``_markers.py`` under ``_markers`` at collection time.

    Tests in this package deliberately have no ``__init__.py`` (aligns with
    ``packages/roxabi-nats/tests`` — a package-level ``__init__.py`` makes
    two conftest modules collide under ``tests.conftest``). Without a
    package, ``from _markers import ...`` from sibling test modules would
    depend on pytest-specific sys.path injection, which ``--import-mode=importlib``
    disables. Use the same spec-from-file-location pattern that
    ``packages/roxabi-nats/tests/conftest.py`` uses for ``_stub_fixture.py``.
    """
    if "_markers" in sys.modules:
        return
    path = Path(__file__).parent / "_markers.py"
    spec = importlib.util.spec_from_file_location("_markers", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to build import spec for _markers")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_markers"] = mod
    spec.loader.exec_module(mod)


_register_markers()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def nats_server_url() -> Generator[str, None, None]:
    if shutil.which("nats-server") is None:
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
