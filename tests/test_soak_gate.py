"""Tests for the soak-gate script (tools/check_worker_error_soak_gate.sh).

Runs the script via subprocess against bundled fixtures to verify that
the PASS/FAIL detection and exit-code contract hold.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "tools" / "check_worker_error_soak_gate.sh"


def _run(fixture: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT), "--fixture", str(REPO / "tests" / "fixtures" / fixture)],
        capture_output=True,
        text=True,
        check=False,
    )


class TestSoakGateScript:
    def test_pass_fixture_exits_zero_and_prints_pass(self) -> None:
        """Script exits 0 and prints PASS for a clean soak log."""
        cp = _run("soak_gate_pass.log")
        assert cp.returncode == 0
        assert cp.stdout.strip() == "PASS"

    def test_fail_fixture_exits_one_and_prints_fail(self) -> None:
        """Script exits 1 and prints FAIL for a log containing error envelopes."""
        cp = _run("soak_gate_fail.log")
        assert cp.returncode == 1
        assert cp.stdout.strip() == "FAIL"
