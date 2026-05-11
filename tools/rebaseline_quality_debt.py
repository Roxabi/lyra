#!/usr/bin/env python3
"""Regenerate tools/quality_debt_baseline.json from the latest audit report.

Reads ``artifacts/quality-debt-report.json`` and emits the baseline JSON the
ratchet (``tools/check_quality_debt_ratchet.sh``) consumes. The baseline:

- carries the ``generated_by`` sentinel the ratchet validates
- pins a ``cutover_date`` = today + 7 days (auto soft → hard cutover)
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


def _build_baseline(report: dict) -> dict:  # type: ignore[type-arg]
    counts: dict[str, dict] = report.get("counts_by_rule_bucket_slug", {})  # type: ignore[type-arg]
    cutover = date.today() + timedelta(days=SOFT_WINDOW_DAYS)
    return {
        "generated_by": GENERATED_BY,
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "cutover_date": cutover.isoformat(),
        "counts": counts,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--report", type=Path, required=True,
                   help="path to audit report JSON")
    p.add_argument("--baseline", type=Path, required=True,
                   help="path to baseline JSON to write")
    args = p.parse_args(argv)

    if not args.report.exists():
        print(f"report not found: {args.report}", file=sys.stderr)
        return 1
    report = json.loads(args.report.read_text(encoding="utf-8"))
    baseline = _build_baseline(report)
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
