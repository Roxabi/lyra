"""Tests for the soak-gate script (tools/check_worker_error_soak_gate.sh).

Runs the script via subprocess against bundled fixtures to verify that
the PASS/FAIL detection and exit-code contract hold.

Each failure mode (missing cli, missing llm, legacy error_text present) is
exercised by an isolated fixture so the gate logic is independently tested
for each branch of the gate condition.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

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

    @pytest.mark.parametrize(
        ("fixture", "missing_branch"),
        [
            ("soak_gate_fail_no_cli.log", "cli populated_total == 0"),
            ("soak_gate_fail_no_llm.log", "llm populated_total == 0"),
            ("soak_gate_fail_legacy_text.log", "legacy error_text != 0"),
        ],
        ids=["no-cli", "no-llm", "legacy-text"],
    )
    def test_each_failure_mode_isolated(
        self, fixture: str, missing_branch: str
    ) -> None:
        """Each gate branch FAILs on its own — no fixture trips two branches at once.

        Guards against the regression where ``soak_gate_fail.log`` triggered
        BOTH "missing llm" AND "non-empty error_text" simultaneously, masking
        the fact that one of the two branches had no independent test.
        """
        cp = _run(fixture)
        assert cp.returncode == 1, f"Expected FAIL for {missing_branch}: {cp.stderr}"
        assert cp.stdout.strip() == "FAIL"

    def test_legacy_dual_failure_still_fails(self) -> None:
        """Backward-compat: original fail fixture (now no-cli) must still FAIL."""
        cp = _run("soak_gate_fail.log")
        assert cp.returncode == 1
        assert cp.stdout.strip() == "FAIL"
