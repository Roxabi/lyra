#!/usr/bin/env python3
"""Verify CI pytest partitions are disjoint and cover each pool (collect-only).

Partition shapes come from tools/pytest_partitions.py (SSoT). Also verifies
.github/workflows/ci.yml invokes scripts/ci-pytest.sh for each runtime partition.

Exit 0 = partitions OK.
Exit 1 = overlap, coverage gap, or ci.yml drift.
Exit 2 = pytest error.

Usage:
    PYTHONPATH=src uv run python tools/check_pytest_partition.py
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_partitions():
    path = REPO_ROOT / "tools" / "pytest_partitions.py"
    spec = importlib.util.spec_from_file_location("pytest_partitions", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load partition SSoT: {path}")
    mod = importlib.util.module_from_spec(spec)
    # dataclasses look up the module in sys.modules during class body processing
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


_partitions = _load_partitions()
PytestPartition = _partitions.PytestPartition
gate_partition_groups = _partitions.gate_partition_groups
verify_ci_yml_sync = _partitions.verify_ci_yml_sync


@dataclass(frozen=True)
class Partition:
    name: str
    args: tuple[str, ...]


def _partition_args(spec: PytestPartition) -> tuple[str, ...]:
    return tuple(spec.pytest_args())


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


def _validate_group(
    total_name: str,
    specs: list[tuple[PytestPartition, str]],
) -> tuple[bool | None, dict[str, int]]:
    """Validate one partition group.

    Returns:
        (True, counts) — disjoint/cover OK
        (False, counts) — partition math violation
        (None, {}) — pytest collect failed (caller should exit 2)
    """
    total_spec = specs[0][0]
    part_specs = specs[1:]

    total = Partition(total_name, _partition_args(total_spec))
    parts = [
        (Partition(label, _partition_args(spec)), label) for spec, label in part_specs
    ]

    try:
        total_ids = _collect_ids(total)
        part_ids = [(p, _collect_ids(p)) for p, _ in parts]
    except RuntimeError:
        return None, {}

    ok = True
    for i, (a_part, a_ids) in enumerate(part_ids):
        for b_part, b_ids in part_ids[i + 1 :]:
            ok = _check_disjoint(a_part, a_ids, b_part, b_ids) and ok
    ok = _check_cover(total_name, total_ids, part_ids) and ok

    counts = {total_name: len(total_ids)}
    for label, ids in zip((lbl for _, lbl in parts), (ids for _, ids in part_ids)):
        counts[label] = len(ids)
    return ok, counts


def main() -> int:
    ci_errors = verify_ci_yml_sync()
    if ci_errors:
        for err in ci_errors:
            print(f"FAIL: {err}", file=sys.stderr)

    factory_group, nats_group = gate_partition_groups()
    ok = not ci_errors
    collect_failed = False
    counts: dict[str, int] = {}

    for total_name, specs in (factory_group, nats_group):
        group_ok, group_counts = _validate_group(total_name, list(specs))
        if group_ok is None:
            collect_failed = True
            continue
        ok = group_ok and ok
        counts.update(group_counts)

    print(
        "check_pytest_partition:",
        f"factory unit={counts.get('factory_unit', 0)}",
        f"infra={counts.get('factory_infra', 0)}",
        f"integration={counts.get('factory_integration', 0)}",
        f"total={counts.get('factory_total', 0)};",
        f"roxabi-nats coverage={counts.get('roxabi_nats_coverage', 0)}",
        f"subprocess={counts.get('roxabi_nats_subprocess', 0)}",
        f"total={counts.get('roxabi_nats_total', 0)}",
    )

    if collect_failed:
        return 2
    if ok:
        print("check_pytest_partition: OK")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
