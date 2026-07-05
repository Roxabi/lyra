"""Tests for scripts/check_seed_age.py::run() — #2246 Slice S2 Task 7.

Contract under test (pinned verbatim in
artifacts/plans/2246-enforce-nkey-rotation-policy-plan.mdx, Task 7 / Task 8):

    run(matrix, rotation_log_path, seeds_dir, *, today=None)
        -> tuple[list[str], list[str], list[str]]   # (warnings, failures, notes)

Pure function — it *returns* strings, it never prints (printing is the CLI's
job, Task 9 / scripts/gen_nkeys.py). WARN_DAYS=75, FAIL_DAYS=90.

Bootstrap-grace design (spec §Expected Behavior items 7-9, §Edge Cases):
  - An identity is warned/failed ONLY when it has >=1 rotation-log.md entry
    for its `secret:` name; age = today - the latest logged date for that
    name.
  - Zero log entries for an active identity -> ALWAYS ok, regardless of the
    identity's `{name}.seed` mtime (bootstrap grace; mtime is
    sync/rsync-resettable and unreliable as a gating signal). If a seed
    file exists for such an identity, its mtime is surfaced as a purely
    informational string in `notes` — never feeds `warnings`/`failures`.
  - `secret:` values that don't name an *active* identity in the matrix
    (unknown name, or a retired identity) are ignored entirely — no
    warning, no failure, no note.
  - Malformed log lines are skipped defensively; must never crash the
    checker.
  - Multiple log lines for the same identity: the *latest date* wins, not
    simply the last matching line in the file (the log is not guaranteed to
    be strictly chronological).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import cast

import pytest
from scripts._acl_models import Identity, LoadedMatrix
from scripts.check_seed_age import run

# ── Fixed synthetic "today" — never real wall-clock time ────────────────────
_TODAY = date(2026, 7, 5)


# ── Minimal LoadedMatrix-shaped fixtures (mirrors the tiny fixture matrices
#    used by tests/scripts/test_genkeys_modes_add_identity.py) ──────────────


def _identity(status: str) -> Identity:
    return cast(
        "Identity",
        {
            "status": status,
            "created_at": "2026-01-01",
            "owner": "factory",
            "description": "Test identity.",
            "allow_responses": False,
            "publish": ["factory.test.>"],
            "subscribe": ["factory.test.>"],
        },
    )


def _matrix(**identities: str) -> LoadedMatrix:
    """Build a LoadedMatrix-shaped dict; kwargs are name=status pairs."""
    return cast(
        "LoadedMatrix",
        {
            "version": "2",
            "request_reply_flows": [],
            "identities": {
                name: _identity(status) for name, status in identities.items()
            },
        },
    )


def _age_date(age_days: int) -> date:
    return _TODAY - timedelta(days=age_days)


def _log_line(
    log_date: date,
    secret: str,
    *,
    reason: str = "seed-generated",
    trigger: str = "factory-acl-add-identity",
) -> str:
    """One rotation-log.md line, matching deploy/lib/operator-log.sh:77 exactly:

    {ts} | secret:{name} | reason:{reason} | by:{user} | trigger:{trigger} | host:{host}
    """
    return (
        f"{log_date.isoformat()} | secret:{secret} | reason:{reason} |"
        f" by:mickael | trigger:{trigger} | host:roxabituwer"
    )


def _write_log(tmp_path: Path, lines: list[str]) -> Path:
    """Write a rotation-log.md fixture with the real header + given lines."""
    path = tmp_path / "rotation-log.md"
    header = "# Factory credential rotation log\n\n"
    body = "\n".join(lines) + ("\n" if lines else "")
    path.write_text(header + body)
    return path


def _touch_seed(seeds_dir: Path, name: str, age_days: int) -> Path:
    """Create {name}.seed with an mtime `age_days` old relative to _TODAY."""
    seeds_dir.mkdir(parents=True, exist_ok=True)
    seed = seeds_dir / f"{name}.seed"
    seed.write_bytes(b"SUAFAKESEEDNOTREAL")
    ts = time.mktime(_age_date(age_days).timetuple())
    os.utime(seed, (ts, ts))
    return seed


# ── Boundary cases (WARN_DAYS=75, FAIL_DAYS=90) ──────────────────────────────


class TestWarnFailBoundaries:
    """Age is computed ONLY from a real rotation-log.md entry.

    Falsifiable: deleting the WARN_DAYS/FAIL_DAYS threshold guard (or
    collapsing it to always "ok") makes every parametrized case return
    (empty, empty), which fails the 75/89/90/91-day assertions below.
    """

    @pytest.mark.parametrize(
        "age_days,expect_warn,expect_fail",
        [
            (74, False, False),
            (75, True, False),
            (89, True, False),
            (90, False, True),
            (91, False, True),
        ],
    )
    def test_boundary(
        self,
        tmp_path: Path,
        age_days: int,
        expect_warn: bool,
        expect_fail: bool,
    ) -> None:
        # Arrange
        matrix = _matrix(**{"telegram-adapter": "active"})
        rotation_log = _write_log(
            tmp_path, [_log_line(_age_date(age_days), "telegram-adapter")]
        )
        seeds_dir = tmp_path / "nkeys"

        # Act
        warnings, failures, notes = run(matrix, rotation_log, seeds_dir, today=_TODAY)

        # Assert
        assert notes == []
        if expect_warn:
            assert len(warnings) == 1
            assert "telegram-adapter" in warnings[0]
            assert failures == []
        elif expect_fail:
            assert len(failures) == 1
            assert "telegram-adapter" in failures[0]
            assert warnings == []
        else:
            assert warnings == []
            assert failures == []


# ── Bootstrap grace (zero log entries) ──────────────────────────────────────


class TestBootstrapGraceWithSeedFile:
    def test_zero_entries_with_old_seed_mtime_never_gates(self, tmp_path: Path) -> None:
        """telegram-adapter has ZERO rotation-log entries; its seed mtime is
        120d old — would hard-FAIL outright if mtime gated verdicts. hub has
        a fresh (5d) real log entry and must stay silent. Bootstrap grace:
        telegram-adapter must never appear in warnings/failures, only in an
        informational note.

        Falsifiable: if the "no log entry -> grace" guard is deleted and the
        implementation falls back to gating on seed mtime, telegram-adapter's
        120d-old mtime would surface as a FAIL, breaking this assertion.
        """
        # Arrange
        matrix = _matrix(**{"telegram-adapter": "active", "hub": "active"})
        rotation_log = _write_log(tmp_path, [_log_line(_age_date(5), "hub")])
        seeds_dir = tmp_path / "nkeys"
        _touch_seed(seeds_dir, "telegram-adapter", 120)

        # Act
        warnings, failures, notes = run(matrix, rotation_log, seeds_dir, today=_TODAY)

        # Assert
        assert warnings == []
        assert failures == []
        assert len(notes) == 1
        assert "telegram-adapter" in notes[0]
        assert "hub" not in notes[0]


class TestBootstrapGraceNoSeedFile:
    def test_zero_entries_and_no_seed_file_is_silent(self, tmp_path: Path) -> None:
        """First-ever run: no rotation-log.md file exists at all, and the
        identity has no {name}.seed file either. Grace applies and there is
        nothing to hint about, so `notes` stays empty too. Must not crash on
        a missing rotation-log.md file.
        """
        # Arrange
        matrix = _matrix(**{"new-adapter": "active"})
        rotation_log = tmp_path / "rotation-log.md"  # never created
        seeds_dir = tmp_path / "nkeys"
        seeds_dir.mkdir()  # exists but empty — no new-adapter.seed

        # Act
        warnings, failures, notes = run(matrix, rotation_log, seeds_dir, today=_TODAY)

        # Assert
        assert warnings == []
        assert failures == []
        assert notes == []


# ── Defensive parsing ─────────────────────────────────────────────────────


class TestMalformedLineSkipped:
    def test_malformed_line_skipped_without_crashing(self, tmp_path: Path) -> None:
        """A hand-edited / corrupted line that doesn't match
        operator-log.sh's pipe-delimited format must be skipped silently —
        not crash the checker, and not be mistaken for a valid entry. A real
        91d-old entry for the same identity, sitting right next to the
        garbage line, must still be found and gate a FAIL.

        Falsifiable: if the guard that skips non-matching lines is removed
        and the parser instead raises (or partially/incorrectly parses the
        garbage line), this test either crashes or produces a wrong verdict.
        """
        # Arrange
        matrix = _matrix(**{"telegram-adapter": "active"})
        garbage = "2026-06-01 secret:telegram-adapter reason:oops-no-pipes"
        rotation_log = _write_log(
            tmp_path,
            [garbage, _log_line(_age_date(91), "telegram-adapter")],
        )
        seeds_dir = tmp_path / "nkeys"

        # Act
        warnings, failures, notes = run(matrix, rotation_log, seeds_dir, today=_TODAY)

        # Assert — no exception raised (implicit); the real entry still gates FAIL
        assert warnings == []
        assert len(failures) == 1
        assert "telegram-adapter" in failures[0]
        assert notes == []


class TestNonActiveSecretIgnored:
    def test_unknown_and_retired_secrets_ignored_entirely(self, tmp_path: Path) -> None:
        """`secret:` values that don't name an ACTIVE identity in the matrix
        must be ignored completely — no warning, no failure, no note — even
        when their logged age would otherwise hard-FAIL. Covers both a name
        absent from the matrix entirely (e.g. `blobstore`, a non-nkey
        secret sharing the same log per the spec) and a name present but
        `status: retired` (`old-worker`).

        Falsifiable: if the active-identity-set filter is deleted,
        blobstore's and old-worker's 95d-old entries would surface as FAIL
        lines.
        """
        # Arrange
        matrix = _matrix(**{"telegram-adapter": "active", "old-worker": "retired"})
        rotation_log = _write_log(
            tmp_path,
            [
                _log_line(_age_date(95), "blobstore"),
                _log_line(_age_date(95), "old-worker"),
                _log_line(_age_date(10), "telegram-adapter"),
            ],
        )
        seeds_dir = tmp_path / "nkeys"

        # Act
        warnings, failures, notes = run(matrix, rotation_log, seeds_dir, today=_TODAY)

        # Assert — only telegram-adapter is in scope, and it is fresh (10d)
        assert warnings == []
        assert failures == []
        assert notes == []


class TestLatestDateWinsNotLastLine:
    def test_out_of_order_lines_use_max_date_not_last_line(
        self, tmp_path: Path
    ) -> None:
        """rotation-log.md is append-only but not guaranteed strictly
        chronological (hand edits, clock skew). The FRESH (10d) entry is
        written FIRST and the STALE (91d) entry LAST — a naive
        "last-matching-line-wins" implementation would incorrectly resolve
        to 91d and FAIL. The correct behavior (max date across all matching
        lines for that identity) resolves to 10d and stays silent.

        Falsifiable: a "keep the last matching line" implementation flips
        this test to a FAIL; a "keep the max date" implementation does not.
        """
        # Arrange
        matrix = _matrix(**{"telegram-adapter": "active"})
        rotation_log = _write_log(
            tmp_path,
            [
                _log_line(_age_date(10), "telegram-adapter"),
                _log_line(_age_date(91), "telegram-adapter"),
            ],
        )
        seeds_dir = tmp_path / "nkeys"

        # Act
        warnings, failures, notes = run(matrix, rotation_log, seeds_dir, today=_TODAY)

        # Assert
        assert warnings == []
        assert failures == []
        assert notes == []


# ── N5 — CLI wiring (`factory-acl check seed-age`), not just run() ──────────
#
# The tests above exercise scripts/check_seed_age.py::run() directly. None of
# them touch _cmd_check_seed_age's argparse wiring (subcommand registration,
# ROTATION_LOG env read + expanduser, _seeds_dir() resolution, the
# WARN:/FAIL:/NOTE: print + exit-code contract) — the exact integration
# surface that ships to the M1 systemd timer. A wiring bug there (wrong
# dest, mis-set default, wrong call signature into run()) would only surface
# in production. These tests invoke the real CLI via subprocess.

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CLI_MATRIX_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "v2-prod.json"


def _run_check_seed_age(
    *, rotation_log: Path, seeds_dir: Path, matrix: Path = _CLI_MATRIX_FIXTURE
) -> subprocess.CompletedProcess[str]:
    """Run `factory-acl check seed-age` via subprocess — real argparse wiring."""
    env = os.environ.copy()
    env["ROTATION_LOG"] = str(rotation_log)
    env["SEEDS_DIR"] = str(seeds_dir)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.gen_nkeys",
            "check",
            "seed-age",
            "--matrix",
            str(matrix),
        ],
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
        env=env,
        timeout=30,
    )


class TestCliWiring:
    """factory-acl check seed-age — CLI/argparse wiring per spec N5."""

    def test_no_rotation_log_is_bootstrap_grace_and_prints_ok(
        self, tmp_path: Path
    ) -> None:
        """No rotation-log.md at all -> every identity in grace, exit 0, OK line."""
        result = _run_check_seed_age(
            rotation_log=tmp_path / "rotation-log.md",  # does not exist
            seeds_dir=tmp_path / "nkeys",  # empty, no seed files either
        )
        assert result.returncode == 0, result.stderr
        assert "check-seed-age: OK (0 warning(s))" in result.stdout
        assert "FAIL:" not in result.stderr
        assert "WARN:" not in result.stderr

    def test_fresh_log_entries_exit_zero_and_print_ok(self, tmp_path: Path) -> None:
        """A rotation-log.md entry logged today for every identity -> clean OK."""
        today = datetime.now(timezone.utc).date()
        rotation_log = tmp_path / "rotation-log.md"
        lines = [
            f"{today.isoformat()} | secret:{name} | reason:seed-generated |"
            " by:mickael | trigger:factory-acl-genkeys | host:roxabituwer"
            for name in ("hub", "voice-tts", "clipool-worker")
        ]
        rotation_log.write_text(
            "# Factory credential rotation log\n\n" + "\n".join(lines) + "\n"
        )

        result = _run_check_seed_age(
            rotation_log=rotation_log, seeds_dir=tmp_path / "nkeys"
        )

        assert result.returncode == 0, result.stderr
        assert "check-seed-age: OK (0 warning(s))" in result.stdout
        assert "WARN:" not in result.stderr
        assert "FAIL:" not in result.stderr

    def test_warn_age_entry_exits_zero_with_warn_and_ok_count(
        self, tmp_path: Path
    ) -> None:
        """An 80-day-old entry (WARN_DAYS<=80<FAIL_DAYS) warns but still exits 0."""
        stale = datetime.now(timezone.utc).date() - timedelta(days=80)
        rotation_log = tmp_path / "rotation-log.md"
        rotation_log.write_text(
            "# Factory credential rotation log\n\n"
            f"{stale.isoformat()} | secret:hub | reason:seed-generated |"
            " by:mickael | trigger:factory-acl-genkeys | host:roxabituwer\n"
        )

        result = _run_check_seed_age(
            rotation_log=rotation_log, seeds_dir=tmp_path / "nkeys"
        )

        assert result.returncode == 0, result.stderr
        assert "WARN:" in result.stderr
        assert "check-seed-age: OK (1 warning(s))" in result.stdout

    def test_fail_age_entry_exits_nonzero_and_omits_ok_line(
        self, tmp_path: Path
    ) -> None:
        """A 95-day-old entry (>=FAIL_DAYS) fails the check; no OK line on failure."""
        stale = datetime.now(timezone.utc).date() - timedelta(days=95)
        rotation_log = tmp_path / "rotation-log.md"
        rotation_log.write_text(
            "# Factory credential rotation log\n\n"
            f"{stale.isoformat()} | secret:hub | reason:seed-generated |"
            " by:mickael | trigger:factory-acl-genkeys | host:roxabituwer\n"
        )

        result = _run_check_seed_age(
            rotation_log=rotation_log, seeds_dir=tmp_path / "nkeys"
        )

        assert result.returncode == 1, result.stdout
        assert "FAIL:" in result.stderr
        assert "check-seed-age: OK" not in result.stdout
