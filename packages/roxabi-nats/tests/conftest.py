"""Shared fixtures for NatsBus unit tests.

Uses a real nats-server subprocess on an ephemeral port — no mocks.
nats-server binary must be in PATH (installed via ``make nats-install``,
or available as a system package). Tests that depend on the ``nc``
fixture are automatically skipped when nats-server is not found.
"""

from __future__ import annotations

import importlib.util
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import AsyncGenerator, Generator
from pathlib import Path

import pytest
from nats.aio.client import Client as NATS

import nats
from roxabi_nats import _version_check as _vc_mod


def _register_stub_fixture() -> None:
    """Register ``_stub_fixture.py`` under ``roxabi_nats_test_stub`` at collection time.

    Resolver tests construct ``_TypeHintResolver([("roxabi_nats_test_stub", ...)])``.
    Keeping the stub a sibling of this ``conftest.py`` excludes it from the
    wheel (hatch only packages ``src/roxabi_nats/``) while preserving a stable
    absolute module path for ``importlib.import_module``.
    """
    if "roxabi_nats_test_stub" in sys.modules:
        return
    path = Path(__file__).parent / "_stub_fixture.py"
    spec = importlib.util.spec_from_file_location("roxabi_nats_test_stub", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to build import spec for roxabi_nats_test_stub")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["roxabi_nats_test_stub"] = mod
    spec.loader.exec_module(mod)


_register_stub_fixture()


@pytest.fixture(autouse=True)
def _reset_version_check_log_state() -> None:
    """Clear the module-level log rate-limit state before every test.

    ``roxabi_nats._version_check`` holds a process-wide dict of last-log
    timestamps so repeat drops within 60 s are silent.  Tests that assert on
    ``log.error`` firing would be flaky across test ordering without this
    reset, since one test's logged drop would silence another's.
    """
    _vc_mod._reset_log_state()


# Skip marker applied automatically to any test that depends on nats-server.
_nats_server_available = shutil.which("nats-server") is not None
requires_nats_server = pytest.mark.skipif(
    not _nats_server_available,
    reason=("nats-server not found in PATH — install via 'make nats-install'"),
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
async def nc(nats_server_url: str) -> AsyncGenerator[NATS, None]:
    """Return a connected nats.NATS client, drained after each test."""
    conn = await nats.connect(nats_server_url)
    yield conn
    if conn.is_connected:
        await conn.drain()
