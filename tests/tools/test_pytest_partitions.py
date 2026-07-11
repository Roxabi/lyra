"""Unit tests for tools/pytest_partitions.py + pure gate helpers.

Negative coverage for the SSoT contracts that the collect-only smoke
(test_check_pytest_partition.sh) cannot falsify alone.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest
from tools.check_pytest_partition import (
    Partition,
    _check_cover,
    _check_disjoint,
)
from tools.pytest_partitions import (
    CI_PARTITIONS,
    CI_YML_PARTITION_CALLS,
    main,
    verify_ci_yml_sync,
)

from tools import check_pytest_partition as gate

REPO = Path(__file__).resolve().parents[2]


def _silence_stderr():
    old = sys.stderr
    sys.stderr = io.StringIO()
    return old


def test_ci_yml_tripwire_covers_all_runtime_partitions() -> None:
    """Tripwire list is derived from CI_PARTITIONS — no silent subset."""
    call_names = {name for name, _ in CI_YML_PARTITION_CALLS}
    assert call_names == set(CI_PARTITIONS), (
        f"CI_YML_PARTITION_CALLS {sorted(call_names)} "
        f"!= CI_PARTITIONS {sorted(CI_PARTITIONS)}"
    )
    for name, needle in CI_YML_PARTITION_CALLS:
        assert needle == f"ci-pytest.sh {name}"


def test_verify_ci_yml_sync_ok_on_repo() -> None:
    assert verify_ci_yml_sync(REPO / ".github/workflows/ci.yml") == []


def test_verify_ci_yml_sync_fails_when_needle_missing(tmp_path: Path) -> None:
    drifted = tmp_path / "ci.yml"
    drifted.write_text(
        "# incomplete\nbash scripts/ci-pytest.sh factory_unit\n",
        encoding="utf-8",
    )
    errors = verify_ci_yml_sync(drifted)
    assert errors, "expected tripwire errors on incomplete ci.yml"
    assert any("roxabi_contracts" in e or "roxabi_obs" in e for e in errors)


def test_emit_unknown_partition_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["emit", "not_a_real_partition"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "unknown partition" in err
    assert "factory_unit" in err


def test_emit_known_partitions_match_ssot(capsys: pytest.CaptureFixture[str]) -> None:
    for name, partition in CI_PARTITIONS.items():
        capsys.readouterr()  # clear
        rc = main(["emit", name])
        assert rc == 0, name
        out = capsys.readouterr().out
        emitted = out.splitlines()
        assert emitted == partition.pytest_args(), name


def test_emit_usage_without_args(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    assert "usage:" in capsys.readouterr().err


def test_check_disjoint_overlap() -> None:
    a = Partition("a", ("tests/",))
    b = Partition("b", ("tests/",))
    ok = _check_disjoint(a, {"tests/x.py::t1"}, b, {"tests/x.py::t1", "tests/y.py::t2"})
    assert ok is False


def test_check_disjoint_clean() -> None:
    a = Partition("a", ("tests/",))
    b = Partition("b", ("tests/",))
    ok = _check_disjoint(a, {"tests/x.py::t1"}, b, {"tests/y.py::t2"})
    assert ok is True


def test_check_cover_missing_only() -> None:
    total = {"tests/a.py::t1", "tests/b.py::t2"}
    parts = [
        (Partition("p1", ()), {"tests/a.py::t1"}),
        (Partition("p2", ()), set()),
    ]
    old = _silence_stderr()
    try:
        ok = _check_cover("total", total, parts)
    finally:
        sys.stderr = old
    assert ok is False


def test_check_cover_extra_only() -> None:
    total = {"tests/a.py::t1"}
    parts = [
        (Partition("p1", ()), {"tests/a.py::t1"}),
        (Partition("p2", ()), {"tests/c.py::t3"}),
    ]
    old = _silence_stderr()
    try:
        ok = _check_cover("total", total, parts)
    finally:
        sys.stderr = old
    assert ok is False


def test_check_cover_ok() -> None:
    total = {"tests/a.py::t1", "tests/b.py::t2"}
    parts = [
        (Partition("p1", ()), {"tests/a.py::t1"}),
        (Partition("p2", ()), {"tests/b.py::t2"}),
    ]
    assert _check_cover("total", total, parts) is True


def test_gate_main_collect_failure_exits_2(monkeypatch: pytest.MonkeyPatch) -> None:
    """Collect RuntimeError must map to exit 2 (not partition-math exit 1)."""

    def boom(_partition: Partition) -> set[str]:
        raise RuntimeError("collect failed: synthetic")

    monkeypatch.setattr(gate, "_collect_ids", boom)
    # Avoid live ci.yml noise if sync were to fail independently
    monkeypatch.setattr(gate, "verify_ci_yml_sync", lambda: [])
    old = _silence_stderr()
    try:
        rc = gate.main()
    finally:
        sys.stderr = old
    assert rc == 2


def test_gate_main_ci_drift_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    """ci.yml tripwire errors alone must yield exit 1 when collect is skipped."""

    monkeypatch.setattr(
        gate,
        "verify_ci_yml_sync",
        lambda: ["ci.yml missing partition invocation: ci-pytest.sh factory_unit"],
    )

    def empty(_partition: Partition) -> set[str]:
        return set()

    # Still need collect to succeed with empty ids so we exercise drift path only
    monkeypatch.setattr(gate, "_collect_ids", empty)
    old = _silence_stderr()
    try:
        rc = gate.main()
    finally:
        sys.stderr = old
    assert rc == 1
