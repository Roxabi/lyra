"""Unit tests for directory↔marker allowlist helpers."""

from __future__ import annotations

from tools.check_pytest_dir_markers import _check_allowlist
from tools.pytest_partitions import MARKER_DIR_ALLOWLIST


def test_allowlist_covers_expected_markers() -> None:
    assert "nats_integration" in MARKER_DIR_ALLOWLIST
    assert "subprocess_nats" in MARKER_DIR_ALLOWLIST
    assert "deploy_contract" in MARKER_DIR_ALLOWLIST


def test_check_allowlist_ok() -> None:
    errs = _check_allowlist(
        "nats_integration",
        ["tests/integration/test_voice_routing.py"],
        MARKER_DIR_ALLOWLIST["nats_integration"],
    )
    assert errs == []


def test_check_allowlist_rejects_wrong_tree() -> None:
    errs = _check_allowlist(
        "nats_integration",
        ["tests/nats/test_nats_bus.py"],
        MARKER_DIR_ALLOWLIST["nats_integration"],
    )
    assert len(errs) == 1
    assert "tests/nats/test_nats_bus.py" in errs[0]
