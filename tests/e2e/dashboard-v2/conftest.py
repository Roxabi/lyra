"""Shared fixtures for dashboard-v2 Playwright e2e."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

_SERVER_SCRIPT = Path(__file__).resolve().parent / "_dashboard_v2_server.py"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def dashboard_v2_url() -> Iterator[str]:
    pytest.importorskip("playwright")
    dist = Path(__file__).resolve().parents[3] / "apps" / "dashboard-v2" / "dist"
    if not (dist / "index.html").is_file():
        pytest.skip(
            "apps/dashboard-v2/dist/index.html missing — run bun run build:dashboard-v2"
        )

    port = _free_port()
    env = os.environ.copy()
    env["FACTORY_DASHBOARD_E2E"] = "1"
    proc = subprocess.Popen(
        [sys.executable, str(_SERVER_SCRIPT), str(port)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    url = f"http://127.0.0.1:{port}/"
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            pytest.fail("dashboard-v2 server exited before ready")
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status == 200:
                    break
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.1)  # event-based
    else:
        proc.terminate()
        pytest.fail("dashboard-v2 server did not become ready")

    yield url
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
