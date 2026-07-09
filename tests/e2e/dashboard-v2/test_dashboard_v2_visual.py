"""Playwright visual regression for factory-dashboard-v2."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest
from PIL import Image, ImageChops

_SNAPSHOT_DIR = Path(__file__).resolve().parent / "snapshots"
_SERVER_SCRIPT = Path(__file__).resolve().parent / "_dashboard_v2_server.py"
_UPDATE = os.environ.get("UPDATE_DASHBOARD_V2_SNAPSHOTS", "").strip() in {
    "1",
    "true",
    "yes",
}
_MAX_DIFF_RATIO = 0.03


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _assert_similar(actual: Path, expected: Path) -> None:
    actual_img = Image.open(actual).convert("RGB")
    expected_img = Image.open(expected).convert("RGB")
    if actual_img.size != expected_img.size:
        actual_img = actual_img.resize(expected_img.size)
    diff = ImageChops.difference(actual_img, expected_img)
    hist = diff.histogram()
    pixels = actual_img.size[0] * actual_img.size[1]
    changed = sum(hist[1:256:3] + hist[2:256:3] + hist[3:256:3])
    ratio = changed / max(pixels * 3, 1)
    assert ratio <= _MAX_DIFF_RATIO, (
        f"visual drift {ratio:.4f} > {_MAX_DIFF_RATIO} for {expected.name}"
    )


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


@pytest.mark.parametrize(
    ("path", "snapshot_prefix"),
    [
        ("chat", "cockpit"),
        ("design-system", "design-system"),
    ],
)
@pytest.mark.parametrize("theme", ["dark", "light"])
def test_dashboard_v2_page_visual(
    dashboard_v2_url: str,
    path: str,
    snapshot_prefix: str,
    theme: str,
    tmp_path: Path,
) -> None:
    from playwright.sync_api import sync_playwright

    snap = _SNAPSHOT_DIR / f"{snapshot_prefix}-{theme}.png"
    shot = tmp_path / f"{snapshot_prefix}-{theme}.png"

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        seed = f"localStorage.setItem('dashboard-v2-theme', {theme!r})"
        page.add_init_script(f"try {{ {seed}; }} catch (e) {{}}")
        page.goto(f"{dashboard_v2_url}{path}", wait_until="networkidle")
        page.wait_for_timeout(300)
        page.screenshot(path=str(shot), full_page=False)
        browser.close()

    if _UPDATE or not snap.is_file():
        _SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(shot, snap)
        pytest.skip(f"baseline written: {snap.name}")

    _assert_similar(shot, snap)
