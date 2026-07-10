#!/usr/bin/env python3
"""Verify CI pytest partitions are disjoint and cover each pool (collect-only).

Mirrors the -m / --ignore expressions in .github/workflows/ci.yml:
  tests job, infra job, integration job, package-coverage roxabi-nats step.

Exit 0 = partitions OK. Exit 1 = overlap or coverage gap. Exit 2 = pytest error.

Usage:
    PYTHONPATH=src uv run python tools/check_pytest_partition.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Keep in sync with .github/workflows/ci.yml pytest invocations.
_FACTORY_UNIT_MARKERS = (
    "not live_acl and not subprocess_nats and not deploy_contract "
    "and not nk_tooling and not nats_integration"
)
_FACTORY_INFRA_MARKERS = "live_acl or subprocess_nats or deploy_contract or nk_tooling"


@dataclass(frozen=True)
class Partition:
    name: str
    args: tuple[str, ...]


def _collect_ids(partition: Partition) -> set[str]:
    cmd = [
        "uv",
        "run",
        "pytest",
        *partition.args,
        "--collect-only",
        "-q",
    ]
    env = os.environ.copy()
    env.setdefault("PYTHONPATH", "src")
    proc = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        print(
            f"ERROR: pytest collect failed for {partition.name} "
            f"(exit {proc.returncode})",
            file=sys.stderr,
        )
        if proc.stderr:
            print(proc.stderr, file=sys.stderr)
        raise RuntimeError(f"collect failed: {partition.name}")

    ids: set[str] = set()
    for line in proc.stdout.splitlines():
        stripped = line.strip()
        if "::" in stripped and stripped.startswith(("tests/", "packages/")):
            ids.add(stripped)
    return ids


def _check_disjoint(
    a: Partition,
    a_ids: set[str],
    b: Partition,
    b_ids: set[str],
) -> bool:
    overlap = a_ids & b_ids
    if not overlap:
        return True
    print(f"FAIL: {a.name} ∩ {b.name} = {len(overlap)} overlap(s)", file=sys.stderr)
    for nodeid in sorted(overlap)[:10]:
        print(f"  {nodeid}", file=sys.stderr)
    if len(overlap) > 10:
        print(f"  … and {len(overlap) - 10} more", file=sys.stderr)
    return False


def _check_cover(
    total_name: str,
    total_ids: set[str],
    parts: list[tuple[Partition, set[str]]],
) -> bool:
    union: set[str] = set()
    for _, ids in parts:
        union |= ids
    missing = total_ids - union
    extra = union - total_ids
    ok = True
    if missing:
        ok = False
        print(
            f"FAIL: {total_name} missing {len(missing)} node(s) from partition union",
            file=sys.stderr,
        )
        for nodeid in sorted(missing)[:10]:
            print(f"  missing: {nodeid}", file=sys.stderr)
        if len(missing) > 10:
            print(f"  … and {len(missing) - 10} more", file=sys.stderr)
    if extra:
        ok = False
        print(
            f"FAIL: partition union has {len(extra)} node(s) outside {total_name}",
            file=sys.stderr,
        )
        for nodeid in sorted(extra)[:10]:
            print(f"  extra: {nodeid}", file=sys.stderr)
        if len(extra) > 10:
            print(f"  … and {len(extra) - 10} more", file=sys.stderr)
    return ok


def main() -> int:
    factory_total = Partition(
        "factory_total",
        ("tests/", "--ignore=tests/e2e"),
    )
    factory_unit = Partition(
        "factory_unit",
        (
            "tests/",
            "--ignore=tests/e2e",
            "-m",
            _FACTORY_UNIT_MARKERS,
        ),
    )
    factory_infra = Partition(
        "factory_infra",
        (
            "tests/",
            "--ignore=tests/integration",
            "--ignore=tests/e2e",
            "-m",
            _FACTORY_INFRA_MARKERS,
        ),
    )
    factory_integration = Partition(
        "factory_integration",
        ("tests/integration/", "-m", "nats_integration"),
    )

    nats_total = Partition("roxabi_nats_total", ("packages/roxabi-nats/tests",))
    nats_coverage = Partition(
        "roxabi_nats_coverage",
        ("packages/roxabi-nats/tests", "-m", "not subprocess_nats"),
    )
    nats_subprocess = Partition(
        "roxabi_nats_subprocess",
        ("packages/roxabi-nats/tests", "-m", "subprocess_nats"),
    )

    try:
        factory_total_ids = _collect_ids(factory_total)
        factory_unit_ids = _collect_ids(factory_unit)
        factory_infra_ids = _collect_ids(factory_infra)
        factory_integration_ids = _collect_ids(factory_integration)
        nats_total_ids = _collect_ids(nats_total)
        nats_coverage_ids = _collect_ids(nats_coverage)
        nats_subprocess_ids = _collect_ids(nats_subprocess)
    except RuntimeError:
        return 2

    ok = True
    factory_parts = [
        (factory_unit, factory_unit_ids),
        (factory_infra, factory_infra_ids),
        (factory_integration, factory_integration_ids),
    ]
    for i, (a_part, a_ids) in enumerate(factory_parts):
        for b_part, b_ids in factory_parts[i + 1 :]:
            ok = _check_disjoint(a_part, a_ids, b_part, b_ids) and ok
    ok = _check_cover("factory_total", factory_total_ids, factory_parts) and ok

    nats_parts = [
        (nats_coverage, nats_coverage_ids),
        (nats_subprocess, nats_subprocess_ids),
    ]
    ok = (
        _check_disjoint(
            nats_coverage,
            nats_coverage_ids,
            nats_subprocess,
            nats_subprocess_ids,
        )
        and ok
    )
    ok = _check_cover("roxabi_nats_total", nats_total_ids, nats_parts) and ok

    print(
        "check_pytest_partition:",
        f"factory unit={len(factory_unit_ids)}",
        f"infra={len(factory_infra_ids)}",
        f"integration={len(factory_integration_ids)}",
        f"total={len(factory_total_ids)};",
        f"roxabi-nats coverage={len(nats_coverage_ids)}",
        f"subprocess={len(nats_subprocess_ids)}",
        f"total={len(nats_total_ids)}",
    )

    if ok:
        print("check_pytest_partition: OK")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
