"""Tests for factory.operator_audit (Python → operator-log.sh bridge)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from factory.operator_audit import op_log, rotation_log_append


def test_op_log_invokes_bash(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    lib = root / "deploy" / "lib"
    lib.mkdir(parents=True)
    (lib / "operator-log.sh").write_text("# stub\n")

    with patch("factory.operator_audit.subprocess.run") as mock_run:
        op_log(root, "evt", action="ping")

    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "bash"
    assert "op_log" in cmd[2]
    assert "evt" in cmd[2]
    assert "action=ping" in cmd[2]


def test_op_log_missing_script_is_noop(tmp_path: Path) -> None:
    with patch("factory.operator_audit.subprocess.run") as mock_run:
        op_log(tmp_path, "evt")
    mock_run.assert_not_called()


def test_rotation_log_append_invokes_bash(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    lib = root / "deploy" / "lib"
    lib.mkdir(parents=True)
    (lib / "operator-log.sh").write_text("# stub\n")

    with patch("factory.operator_audit.subprocess.run") as mock_run:
        rotation_log_append(root, "nats-nkeys", "disaster-recovery", trigger="cli")

    assert "rotation_log_append" in mock_run.call_args[0][0][2]