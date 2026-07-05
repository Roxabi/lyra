#!/usr/bin/env python3
"""check_seed_age.py — warn/fail on stale nkey seeds (rotation-log.md driven).

Consumes:
  deploy/nats/acl-matrix.json     — identity matrix (active identities in scope)
  ~/.roxabi/factory/rotation-log.md — human-readable rotation records
                                       (appended by deploy/lib/operator-log.sh's
                                       rotation_log_append())

Policy (ADR-093-adjacent, #2246):
  - An active identity is gated (WARN/FAIL) ONLY when rotation-log.md has at
    least one entry for its ``secret:<name>``. Age = today - latest logged
    date for that name.
  - WARN_DAYS <= age_days < FAIL_DAYS -> warning
  - age_days >= FAIL_DAYS -> failure
  - Zero log entries for an active identity is bootstrap grace: it NEVER
    gates a warning/failure, regardless of its ``{name}.seed`` mtime (mtime
    is sync/rsync-resettable and unreliable as a policy signal). If a seed
    file exists, its mtime is surfaced as a purely informational note.
  - ``secret:`` values that don't name an *active* identity in the matrix
    (unknown name, or a retired identity) are ignored entirely.

This module is a pure function — it returns strings, it never prints.
Printing (``WARN:``/``FAIL:``/``NOTE:`` to stderr) and the exit code are the
CLI's job (``scripts/gen_nkeys.py``'s ``factory-acl check seed-age``).
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from pathlib import Path

from scripts._acl_models import LoadedMatrix

WARN_DAYS = 75
FAIL_DAYS = 90

# Matches deploy/lib/operator-log.sh:77's rotation_log_append() line format:
#   {ts} | secret:{name} | reason:{reason} | by:{user} | trigger:{trigger} | host:{host}
_LOG_LINE_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})\s*\|\s*secret:(?P<secret>\S+)\s*\|"
)


def _latest_log_dates(rotation_log_path: Path) -> dict[str, date]:
    """Parse rotation_log_path, keeping the latest logged date per secret name.

    Malformed/header lines are skipped defensively. Missing file -> empty dict.
    """
    latest: dict[str, date] = {}
    try:
        text = rotation_log_path.read_text()
    except OSError:
        return latest

    for line in text.splitlines():
        match = _LOG_LINE_RE.match(line.strip())
        if not match:
            continue
        try:
            log_date = date.fromisoformat(match.group("date"))
        except ValueError:
            continue
        secret = match.group("secret")
        prev = latest.get(secret)
        if prev is None or log_date > prev:
            latest[secret] = log_date

    return latest


def run(
    matrix: LoadedMatrix,
    rotation_log_path: Path,
    seeds_dir: Path,
    *,
    today: date | None = None,
) -> tuple[list[str], list[str], list[str]]:
    """Check nkey seed age against rotation-log.md for every active identity.

    Returns ``(warnings, failures, notes)`` — plain strings, never printed
    here. ``today`` defaults to the current UTC date.
    """
    if today is None:
        today = datetime.now(timezone.utc).date()

    latest_dates = _latest_log_dates(rotation_log_path)

    active_names = {
        name
        for name, identity in matrix["identities"].items()
        if identity["status"] == "active"
    }

    warnings: list[str] = []
    failures: list[str] = []
    notes: list[str] = []

    for name in sorted(active_names):
        latest = latest_dates.get(name)
        if latest is not None:
            age_days = (today - latest).days
            if age_days >= FAIL_DAYS:
                failures.append(
                    f"{name}: nkey seed rotation is {age_days}d old"
                    f" (last logged {latest.isoformat()}, FAIL_DAYS={FAIL_DAYS})"
                )
            elif age_days >= WARN_DAYS:
                warnings.append(
                    f"{name}: nkey seed rotation is {age_days}d old"
                    f" (last logged {latest.isoformat()}, WARN_DAYS={WARN_DAYS})"
                )
            continue

        # Bootstrap grace: no rotation-log entry -> never gates a verdict.
        seed_path = seeds_dir / f"{name}.seed"
        try:
            mtime = seed_path.stat().st_mtime
        except OSError:
            continue

        seed_age_days = (
            today - datetime.fromtimestamp(mtime, tz=timezone.utc).date()
        ).days
        notes.append(
            f"{name} has no rotation-log entry; seed mtime is {seed_age_days}d"
            " old (informational, not policy-enforced — bootstrap grace"
            " applies)"
        )

    return warnings, failures, notes
