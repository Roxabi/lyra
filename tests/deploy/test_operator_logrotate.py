"""Tests for deploy/factory-operator-logrotate.sh."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LOGROTATE_SCRIPT = REPO_ROOT / "deploy" / "factory-operator-logrotate.sh"


@pytest.fixture
def home_tmp(tmp_path: Path) -> Path:
    root = tmp_path / "home"
    root.mkdir()
    return root


def test_operator_logrotate_writes_config(home_tmp: Path) -> None:
    log_path = home_tmp / ".local" / "state" / "factory" / "logs" / "operator.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text('{"event":"test"}\n' * 100)

    result = subprocess.run(
        [str(LOGROTATE_SCRIPT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "HOME": str(home_tmp),
            "XDG_STATE_HOME": str(home_tmp / ".local" / "state"),
        },
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    conf = home_tmp / ".config" / "logrotate" / "factory-operator.conf"
    assert conf.exists()
    conf_text = conf.read_text()
    assert str(log_path) in conf_text
    assert "rotate 12" in conf_text
    assert "maxsize 10M" in conf_text