"""SSoT for CI pytest job partitions.

Consumed by:
  - scripts/ci-pytest.sh (runtime invocations in .github/workflows/ci.yml)
  - tools/check_pytest_partition.py (collect-only disjoint/cover checks)
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

FACTORY_UNIT_MARKERS = (
    "not live_acl and not subprocess_nats and not deploy_contract "
    "and not nk_tooling and not nats_integration"
)
FACTORY_INFRA_MARKERS = "live_acl or subprocess_nats or deploy_contract or nk_tooling"
NAT_COVERAGE_MARKERS = "not subprocess_nats"
NAT_SUBPROCESS_MARKERS = "subprocess_nats"
INTEGRATION_MARKERS = "nats_integration"

# ci.yml must invoke scripts/ci-pytest.sh for these partitions (sync tripwire).
CI_YML_PARTITION_CALLS: tuple[tuple[str, str], ...] = (
    ("factory_unit", "ci-pytest.sh factory_unit"),
    ("factory_infra", "ci-pytest.sh factory_infra"),
    ("factory_integration", "ci-pytest.sh factory_integration"),
    ("roxabi_nats", "ci-pytest.sh roxabi_nats"),
)


@dataclass(frozen=True)
class PytestPartition:
    """A named pytest invocation shape used in CI."""

    name: str
    paths: tuple[str, ...]
    ignores: tuple[str, ...] = ()
    markers: str | None = None

    def pytest_args(self) -> list[str]:
        args: list[str] = list(self.paths)
        for ign in self.ignores:
            args.extend(["--ignore", ign])
        if self.markers is not None:
            args.extend(["-m", self.markers])
        return args


CI_PARTITIONS: dict[str, PytestPartition] = {
    "factory_unit": PytestPartition(
        "factory_unit",
        ("tests/",),
        ignores=("tests/e2e",),
        markers=FACTORY_UNIT_MARKERS,
    ),
    "factory_infra": PytestPartition(
        "factory_infra",
        ("tests/",),
        ignores=("tests/integration", "tests/e2e"),
        markers=FACTORY_INFRA_MARKERS,
    ),
    "factory_integration": PytestPartition(
        "factory_integration",
        ("tests/integration/",),
        markers=INTEGRATION_MARKERS,
    ),
    "roxabi_nats": PytestPartition(
        "roxabi_nats",
        ("packages/roxabi-nats/tests",),
    ),
    "roxabi_contracts": PytestPartition(
        "roxabi_contracts",
        ("packages/roxabi-contracts/tests",),
    ),
    "roxabi_obs": PytestPartition(
        "roxabi_obs",
        ("packages/roxabi-obs/tests",),
    ),
}


def gate_partition_groups() -> tuple[
    tuple[str, list[tuple[PytestPartition, str]]],
    tuple[str, list[tuple[PytestPartition, str]]],
]:
    """Return (factory_group, nats_group) for disjoint/cover validation."""
    factory_total = PytestPartition(
        "factory_total",
        ("tests/",),
        ignores=("tests/e2e",),
    )
    factory_parts = [
        (CI_PARTITIONS["factory_unit"], "factory_unit"),
        (CI_PARTITIONS["factory_infra"], "factory_infra"),
        (CI_PARTITIONS["factory_integration"], "factory_integration"),
    ]
    nats_total = PytestPartition("roxabi_nats_total", ("packages/roxabi-nats/tests",))
    nats_parts = [
        (
            PytestPartition(
                "roxabi_nats_coverage",
                ("packages/roxabi-nats/tests",),
                markers=NAT_COVERAGE_MARKERS,
            ),
            "roxabi_nats_coverage",
        ),
        (
            PytestPartition(
                "roxabi_nats_subprocess",
                ("packages/roxabi-nats/tests",),
                markers=NAT_SUBPROCESS_MARKERS,
            ),
            "roxabi_nats_subprocess",
        ),
    ]
    return (
        ("factory_total", [(factory_total, "factory_total"), *factory_parts]),
        ("roxabi_nats_total", [(nats_total, "roxabi_nats_total"), *nats_parts]),
    )


def verify_ci_yml_sync(ci_yml: Path | None = None) -> list[str]:
    """Return error messages when ci.yml drifts from partition SSoT."""
    path = ci_yml or (REPO_ROOT / ".github/workflows/ci.yml")
    text = path.read_text(encoding="utf-8")
    errors: list[str] = []
    for _name, needle in CI_YML_PARTITION_CALLS:
        if needle not in text:
            errors.append(f"ci.yml missing partition invocation: {needle}")
    return errors


def _cmd_emit(partition_name: str) -> int:
    try:
        partition = CI_PARTITIONS[partition_name]
    except KeyError:
        known = ", ".join(sorted(CI_PARTITIONS))
        print(
            f"ERROR: unknown partition {partition_name!r} (known: {known})",
            file=sys.stderr,
        )
        return 2
    for arg in partition.pytest_args():
        print(arg)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if len(args) >= 2 and args[0] == "emit":
        return _cmd_emit(args[1])
    print("usage: pytest_partitions.py emit <partition>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
