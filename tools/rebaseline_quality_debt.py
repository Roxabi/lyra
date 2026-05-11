#!/usr/bin/env python3
"""Regenerate tools/quality_debt_baseline.json from the latest audit report.

Reads ``artifacts/quality-debt-report.json`` and emits the baseline JSON the
ratchet (``tools/check_quality_debt_ratchet.sh``) consumes. The baseline:

- carries the ``generated_by`` sentinel the ratchet validates
- pins a ``cutover_date`` = today + 7 days (auto soft → hard cutover) on
  first creation; preserves the existing cutover_date on subsequent runs
  unless ``--reset-cutover`` is passed
- captures the per-``(rule, bucket, slug)`` counts so future drift can be
  detected without re-running ``gh`` or any external service
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

GENERATED_BY = "make quality-debt-rebaseline"
SOFT_WINDOW_DAYS = 7


def _load_existing(path: Path) -> dict | None:  # type: ignore[type-arg]
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
    except (json.JSONDecodeError, OSError):
        return None


def _warn_no_decrease(new: dict, existing: dict | None) -> None:  # type: ignore[type-arg]
    if not existing:
        return
    old_counts: dict = existing.get("counts", {})
    new_counts: dict = new.get("counts", {})
    for rule, rule_buckets in new_counts.items():
        debt: dict = rule_buckets.get("DEBT", {})
        old_debt: dict = old_counts.get(rule, {}).get("DEBT", {})
        for slug, n in debt.items():
            o: int = old_debt.get(slug, 0)
            if n >= o and o > 0:
                print(
                    f"WARN: no decrease for {rule}/{slug}: {o} -> {n}",
                    file=sys.stderr,
                )


def _build_baseline(
    report: dict,  # type: ignore[type-arg]
    *,
    existing: dict | None = None,  # type: ignore[type-arg]
    reset_cutover: bool = False,
) -> dict:  # type: ignore[type-arg]
    counts: dict[str, dict] = report.get("counts_by_rule_bucket_slug", {})  # type: ignore[type-arg]
    if existing and not reset_cutover and "cutover_date" in existing:
        cutover = existing["cutover_date"]
    else:
        cutover = (date.today() + timedelta(days=SOFT_WINDOW_DAYS)).isoformat()
    return {
        "generated_by": GENERATED_BY,
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "cutover_date": cutover,
        "counts": counts,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--report", type=Path, required=True, help="path to audit report JSON"
    )
    p.add_argument(
        "--baseline", type=Path, required=True, help="path to baseline JSON to write"
    )
    p.add_argument(
        "--reset-cutover",
        action="store_true",
        help="Reset cutover_date to today+7 even if an existing baseline has one set.",
    )
    args = p.parse_args(argv)

    if not args.report.exists():
        print(f"report not found: {args.report}", file=sys.stderr)
        return 1
    report = json.loads(args.report.read_text(encoding="utf-8"))
    existing = _load_existing(args.baseline)
    baseline = _build_baseline(
        report, existing=existing, reset_cutover=args.reset_cutover
    )
    _warn_no_decrease(baseline, existing)
    args.baseline.write_text(
        json.dumps(baseline, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    print(
        f"baseline written: {args.baseline} "
        f"(cutover_date={baseline['cutover_date']}, "
        f"rules={len(baseline['counts'])})",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
