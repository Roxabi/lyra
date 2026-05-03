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

    def test_dual_failure_fixture_fails_on_either_branch(self) -> None:
        """Fixture trips BOTH `pop_llm == 0` AND `legacy_text != 0` simultaneously.

        Guards against a regression where one of the two branches stops firing
        (e.g. counter parsing breaks): the gate must FAIL when either condition
        holds independently, and the original fail fixture exists to assert
        that two-branch failures still surface as a single FAIL exit (not
        masked by short-circuit logic).
        """
        cp = _run("soak_gate_fail.log")
        assert cp.returncode == 1
        assert cp.stdout.strip() == "FAIL"
        # Verify the per-counter breakdown shows both branches tripped.
        # populated_cli=1, populated_llm=0, legacy_text>=1
        assert "populated_llm=0" in cp.stderr
        assert "legacy_text=1" in cp.stderr
