"""Tests for deploy/lib/syncthing-ignore.sh (.stignore idempotent ensure)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SYNCTHING_IGNORE_SH = REPO_ROOT / "deploy" / "lib" / "syncthing-ignore.sh"
STIGNORE_TEMPLATE = REPO_ROOT / "deploy" / "templates" / "factory.stignore"


def _run_bash(script: str, *, home: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(home)},
        check=False,
    )


@pytest.fixture
def home_tmp(tmp_path: Path) -> Path:
    root = tmp_path / "home"
    root.mkdir()
    return root


def test_ensure_factory_stignore_creates_from_template(home_tmp: Path) -> None:
    stignore = home_tmp / ".roxabi" / "factory" / ".stignore"
    result = _run_bash(
        f'source "{SYNCTHING_IGNORE_SH}" && ensure_factory_stignore "{STIGNORE_TEMPLATE}"',
        home=home_tmp,
    )
    assert result.returncode == 0, result.stderr
    text = stignore.read_text()
    assert "nats/jetstream" in text
    assert "blobstore" in text
    assert "blobstore.tok" in text


def test_ensure_factory_stignore_appends_missing_entries(home_tmp: Path) -> None:
    stignore = home_tmp / ".roxabi" / "factory" / ".stignore"
    stignore.parent.mkdir(parents=True)
    stignore.write_text("# existing\nnats/jetstream\nblobstore\n")
    result = _run_bash(
        f'source "{SYNCTHING_IGNORE_SH}" && ensure_factory_stignore "{STIGNORE_TEMPLATE}"',
        home=home_tmp,
    )
    assert result.returncode == 0, result.stderr
    lines = [ln for ln in stignore.read_text().splitlines() if ln and not ln.startswith("#")]
    assert "blobstore.tok" in lines
    assert lines.count("nats/jetstream") == 1
    assert lines.count("blobstore") == 1