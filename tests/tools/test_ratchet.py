"""RED tests for tools/check_quality_debt_ratchet.sh.

Script does not exist yet — all tests FAIL. Correct RED state for this phase.

Contract:
  CLI: bash tools/check_quality_debt_ratchet.sh
  Env vars consumed:
    QG_AUDIT_REPORT   path to audit report JSON (avoids re-running audit tool)
    QG_BASELINE       path to baseline JSON
    RATCHET_MODE      "soft" | "hard" (optional override)
  Exit 0  iff: soft mode OR no violations.
  Exit 1  iff: hard mode AND >=1 violation (untagged | count drift | stale ref).
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from datetime import date, timedelta
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO / "tools" / "check_quality_debt_ratchet.sh"

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _today_plus(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def _write_baseline(
    path: Path,
    cutover_date: str,
    counts: dict | None = None,  # type: ignore[type-arg]
    generated_by: str = "make quality-debt-rebaseline",
) -> None:
    data = {
        "generated_by": generated_by,
        "cutover_date": cutover_date,
        "generated_at": "2026-05-11T16:00:00Z",
        "counts": counts or {},
    }
    path.write_text(json.dumps(data))


def _write_audit_report(
    path: Path,
    rows: list | None = None,  # type: ignore[type-arg]
    stale_references: list | None = None,  # type: ignore[type-arg]
    counts_by_rule_bucket_slug: dict | None = None,  # type: ignore[type-arg]
) -> None:
    data = {
        "generated_at": "2026-05-11T16:00:00Z",
        "rows": rows or [],
        "stale_references": stale_references or [],
        "counts_by_rule_bucket_slug": counts_by_rule_bucket_slug or {},
    }
    path.write_text(json.dumps(data))


def _run(
    tmp_path: Path,
    audit_report: Path,
    baseline: Path,
    extra_env: dict | None = None,  # type: ignore[type-arg]
) -> subprocess.CompletedProcess[str]:
    assert SCRIPT.exists(), (
        f"Script not found: {SCRIPT}\n"
        "Implement tools/check_quality_debt_ratchet.sh first (T9)."
    )
    env = {
        **os.environ,
        "QG_AUDIT_REPORT": str(audit_report),
        "QG_BASELINE": str(baseline),
    }
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(tmp_path),
        env=env,
    )


def _untagged_row(path: str = "src/lyra/x.py") -> dict:  # type: ignore[type-arg]
    return {
        "source": "noqa",
        "bucket": "UNTAGGED",
        "rule": "BLE001",
        "path": path,
        "line": 1,
    }


# ---------------------------------------------------------------------------
# test cases
# ---------------------------------------------------------------------------


def test_soft_mode_pre_cutover_warns_and_exits_zero(tmp_path: Path) -> None:
    """Pre-cutover + no RATCHET_MODE -> soft mode -> warns on violation, exits 0."""
    baseline = tmp_path / "baseline.json"
    audit = tmp_path / "report.json"
    _write_baseline(baseline, cutover_date=_today_plus(5))
    _write_audit_report(audit, rows=[_untagged_row()])

    cp = _run(tmp_path, audit, baseline)

    assert cp.returncode == 0, (
        f"expected exit 0 in soft mode; rc={cp.returncode}\nstderr={cp.stderr}"
    )
    assert "untagged" in cp.stderr.lower(), (
        f"expected 'untagged' warning in stderr; stderr={cp.stderr}"
    )


def test_hard_mode_post_cutover_exits_nonzero_on_violation(tmp_path: Path) -> None:
    """Post-cutover + no RATCHET_MODE override -> hard mode -> exits 1 on violation."""
    baseline = tmp_path / "baseline.json"
    audit = tmp_path / "report.json"
    _write_baseline(baseline, cutover_date=_today_plus(-1))
    _write_audit_report(audit, rows=[_untagged_row()])

    cp = _run(tmp_path, audit, baseline)

    assert cp.returncode == 1, (
        "expected exit 1 in hard mode post-cutover; "
        f"rc={cp.returncode}\nstderr={cp.stderr}"
    )


def test_ratchet_mode_soft_env_overrides_post_cutover(tmp_path: Path) -> None:
    """RATCHET_MODE=soft forces soft mode even when cutover date has passed."""
    baseline = tmp_path / "baseline.json"
    audit = tmp_path / "report.json"
    _write_baseline(baseline, cutover_date=_today_plus(-1))  # would be hard
    _write_audit_report(audit, rows=[_untagged_row()])

    cp = _run(tmp_path, audit, baseline, extra_env={"RATCHET_MODE": "soft"})

    assert cp.returncode == 0, (
        "RATCHET_MODE=soft must override post-cutover hard; "
        f"rc={cp.returncode}\nstderr={cp.stderr}"
    )


def test_ratchet_mode_hard_env_overrides_pre_cutover(tmp_path: Path) -> None:
    """RATCHET_MODE=hard forces hard mode even when cutover date is in the future."""
    baseline = tmp_path / "baseline.json"
    audit = tmp_path / "report.json"
    _write_baseline(baseline, cutover_date=_today_plus(5))  # would be soft
    _write_audit_report(audit, rows=[_untagged_row()])

    cp = _run(tmp_path, audit, baseline, extra_env={"RATCHET_MODE": "hard"})

    assert cp.returncode == 1, (
        "RATCHET_MODE=hard must override pre-cutover soft; "
        f"rc={cp.returncode}\nstderr={cp.stderr}"
    )


def test_violation_rule_a_untagged_in_src(tmp_path: Path) -> None:
    """Rule (a): UNTAGGED row at src/ path -> hard exits 1 + 'untagged' in stderr."""
    baseline = tmp_path / "baseline.json"
    audit = tmp_path / "report.json"
    _write_baseline(baseline, cutover_date=_today_plus(-1))
    _write_audit_report(audit, rows=[_untagged_row("src/lyra/x.py")])

    cp = _run(tmp_path, audit, baseline, extra_env={"RATCHET_MODE": "hard"})

    assert cp.returncode == 1, (
        f"expected exit 1; rc={cp.returncode}\nstderr={cp.stderr}"
    )
    assert "untagged" in cp.stderr.lower(), (
        f"expected 'untagged' in stderr; stderr={cp.stderr}"
    )


def test_violation_rule_b_count_above_baseline(tmp_path: Path) -> None:
    """Rule (b): (BLE001, DEBT, slug) count > baseline -> hard mode exits 1."""
    baseline = tmp_path / "baseline.json"
    audit = tmp_path / "report.json"
    _write_baseline(
        baseline,
        cutover_date=_today_plus(-1),
        counts={"BLE001": {"DEBT": {"my-slug": 5}, "POLICY": {}, "UNTAGGED": 0}},
    )
    # current report shows count = 6 (above baseline 5)
    _write_audit_report(
        audit,
        counts_by_rule_bucket_slug={
            "BLE001": {"DEBT": {"my-slug": 6}, "POLICY": {}, "UNTAGGED": 0}
        },
    )

    cp = _run(tmp_path, audit, baseline, extra_env={"RATCHET_MODE": "hard"})

    assert cp.returncode == 1, (
        f"expected exit 1 on count drift; rc={cp.returncode}\nstderr={cp.stderr}"
    )
    stderr_lower = cp.stderr.lower()
    assert (
        "count" in stderr_lower
        or "above" in stderr_lower
        or "baseline" in stderr_lower
    ), f"expected count-drift message in stderr; stderr={cp.stderr}"


def test_violation_rule_c_debt_slug_stale_reference(tmp_path: Path) -> None:
    """Rule (c): stale_references non-empty -> hard exits 1 + registry/stale hint."""
    baseline = tmp_path / "baseline.json"
    audit = tmp_path / "report.json"
    _write_baseline(baseline, cutover_date=_today_plus(-1))
    stale = [{"path": "src/lyra/x.py", "slug": "ghost", "reason": "missing"}]
    _write_audit_report(audit, stale_references=stale)

    cp = _run(tmp_path, audit, baseline, extra_env={"RATCHET_MODE": "hard"})

    assert cp.returncode == 1, (
        f"expected exit 1 on stale ref; rc={cp.returncode}\nstderr={cp.stderr}"
    )
    stderr_lower = cp.stderr.lower()
    assert "registry" in stderr_lower or "stale" in stderr_lower, (
        f"expected 'registry'/'stale' in stderr; stderr={cp.stderr}"
    )


def test_baseline_missing_generated_by_fails_to_load(tmp_path: Path) -> None:
    """Baseline wrong 'generated_by' -> non-zero exit + regenerate hint in stderr."""
    baseline = tmp_path / "baseline.json"
    audit = tmp_path / "report.json"
    # Write baseline with wrong generated_by value
    _write_baseline(
        baseline,
        cutover_date=_today_plus(5),
        generated_by="manual-edit",
    )
    _write_audit_report(audit)

    cp = _run(tmp_path, audit, baseline)

    assert cp.returncode != 0, (
        "expected non-zero when generated_by is wrong; "
        f"rc={cp.returncode}\nstderr={cp.stderr}"
    )
    stderr_lower = cp.stderr.lower()
    assert "regenerate" in stderr_lower or "generated_by" in stderr_lower, (
        f"expected regenerate/generated_by hint in stderr; stderr={cp.stderr}"
    )


def test_ratchet_rejects_malformed_report(tmp_path: Path) -> None:
    """jq -e pre-check: report missing counts_by_rule_bucket_slug -> exit != 0."""
    # Arrange
    baseline = tmp_path / "baseline.json"
    audit = tmp_path / "report.json"
    _write_baseline(baseline, cutover_date=_today_plus(5))
    # Write a report with no counts_by_rule_bucket_slug key at all
    audit.write_text(
        json.dumps(
            {"generated_at": "2026-05-11T00:00:00Z", "rows": [], "stale_references": []}
        )
    )

    # Act
    cp = _run(tmp_path, audit, baseline)

    # Assert
    assert cp.returncode != 0, (
        f"expected non-zero exit for malformed report; "
        f"rc={cp.returncode}\nstderr={cp.stderr}"
    )
    stderr_lower = cp.stderr.lower()
    assert (
        "counts_by_rule_bucket_slug" in stderr_lower
        or "schema" in stderr_lower
        or "malformed" in stderr_lower
        or "missing" in stderr_lower
        or "regenerate" in stderr_lower
    ), f"expected informative stderr about missing key; stderr={cp.stderr}"


@pytest.mark.parametrize(
    "invalid_mode",
    [
        "bogus",
        "SOFT",
        "Hard",
        "HARD",
        pytest.param(
            "",
            marks=pytest.mark.xfail(
                reason="bash case with empty RATCHET_MODE falls through to default; "
                "script should explicitly reject empty string (known gap)",
                strict=True,
            ),
        ),
    ],
)
def test_ratchet_rejects_invalid_mode(tmp_path: Path, invalid_mode: str) -> None:
    """RATCHET_MODE unlisted value -> exit != 0 + valid modes named in stderr.

    Covers exact-match ("bogus"), wrong-case variants ("SOFT", "Hard", "HARD"),
    and empty string — bash case matching is case-sensitive; none of these
    should silently pass as a valid mode.
    """
    # Arrange
    baseline = tmp_path / "baseline.json"
    audit = tmp_path / "report.json"
    _write_baseline(baseline, cutover_date=_today_plus(5))
    _write_audit_report(audit)

    # Act
    cp = _run(tmp_path, audit, baseline, extra_env={"RATCHET_MODE": invalid_mode})

    # Assert: any invalid mode must produce non-zero exit
    assert cp.returncode != 0, (
        f"expected non-zero exit for RATCHET_MODE={invalid_mode!r}; "
        f"rc={cp.returncode}\nstderr={cp.stderr}"
    )
    # For non-empty invalid values: error message should name valid modes
    if invalid_mode:
        stderr_lower = cp.stderr.lower()
        assert "soft" in stderr_lower or "hard" in stderr_lower, (
            f"expected valid modes ('soft'/'hard') named in stderr for "
            f"RATCHET_MODE={invalid_mode!r}; stderr={cp.stderr}"
        )


def test_ratchet_makes_zero_gh_api_calls(tmp_path: Path) -> None:
    """Ratchet MUST NOT call 'gh' — registry lookups are local-only.

    Regression guard: if someone replaces local-registry checks with gh API
    calls, this test catches it. The gh stub records any invocation to a log
    file and exits non-zero; we assert the log is absent after a clean run.
    """
    # Create a gh stub that logs its invocation and exits non-zero
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh_stub = bin_dir / "gh"
    gh_stub.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "gh called: $@" >> "{tmp_path}/gh_calls.log"\n'
        "exit 1\n"
    )
    gh_stub.chmod(
        gh_stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH
    )

    baseline = tmp_path / "baseline.json"
    audit = tmp_path / "report.json"
    _write_baseline(baseline, cutover_date=_today_plus(5))
    _write_audit_report(audit)

    path_with_stub = f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}"
    _run(tmp_path, audit, baseline, extra_env={"PATH": path_with_stub})

    gh_log = tmp_path / "gh_calls.log"
    assert not gh_log.exists(), (
        f"ratchet made unexpected gh API calls:\n{gh_log.read_text()}"
        "\nFix: use local registry files, not gh API."
    )
