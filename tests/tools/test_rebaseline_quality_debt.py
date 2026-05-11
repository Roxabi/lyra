"""Tests for tools/rebaseline_quality_debt.py private helpers.

Covers T4a (_build_baseline structure + cutover_date logic),
T4b (_load_existing happy / missing / malformed),
T4c (_warn_no_decrease warn / silent / no-existing).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tools.rebaseline_quality_debt import (
    _build_baseline,
    _load_existing,
    _warn_no_decrease,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

SAMPLE_REPORT = {
    "generated_at": "2026-05-11T00:00:00Z",
    "counts_by_rule_bucket_slug": {
        "BLE001": {"DEBT": {"my-slug": 3}, "POLICY": {}, "UNTAGGED": 0},
    },
    "rows": [],
    "stale_references": [],
}


# ---------------------------------------------------------------------------
# T4a — _build_baseline structure
# ---------------------------------------------------------------------------


def test_build_baseline_returns_expected_structure() -> None:
    """Returned dict has exactly the four expected keys and correct counts value."""
    # Arrange
    report = SAMPLE_REPORT

    # Act
    result = _build_baseline(report)

    # Assert
    assert set(result.keys()) == {
        "generated_by",
        "generated_at",
        "cutover_date",
        "counts",
    }
    assert result["counts"] == report["counts_by_rule_bucket_slug"]


def test_build_baseline_preserves_cutover_from_existing() -> None:
    """When an existing baseline with cutover_date exists, that date is preserved."""
    # Arrange
    existing = {"cutover_date": "2030-01-01", "counts": {}}

    # Act
    result = _build_baseline(SAMPLE_REPORT, existing=existing)

    # Assert
    assert result["cutover_date"] == "2030-01-01"


def test_build_baseline_resets_cutover_when_flag_set() -> None:
    """reset_cutover=True causes a fresh cutover_date, overriding the existing one."""
    # Arrange
    existing = {"cutover_date": "2030-01-01", "counts": {}}

    # Act
    result = _build_baseline(SAMPLE_REPORT, existing=existing, reset_cutover=True)

    # Assert
    assert result["cutover_date"] != "2030-01-01"


# ---------------------------------------------------------------------------
# T4b — _load_existing happy / missing / malformed
# ---------------------------------------------------------------------------


def test_load_existing_returns_dict_for_valid_json(tmp_path: Path) -> None:
    """Valid JSON file -> returns the parsed dict."""
    # Arrange
    baseline = tmp_path / "baseline.json"
    data = {"cutover_date": "2026-06-01", "counts": {}}
    baseline.write_text(json.dumps(data), encoding="utf-8")

    # Act
    result = _load_existing(baseline)

    # Assert
    assert result == data


def test_load_existing_returns_none_for_missing_file(tmp_path: Path) -> None:
    """Non-existent file -> returns None."""
    # Arrange
    missing = tmp_path / "no_such_file.json"

    # Act
    result = _load_existing(missing)

    # Assert
    assert result is None


def test_load_existing_returns_none_for_malformed_json(tmp_path: Path) -> None:
    """Malformed JSON file -> returns None (does not raise)."""
    # Arrange
    bad = tmp_path / "bad.json"
    bad.write_text("not valid { json", encoding="utf-8")

    # Act
    result = _load_existing(bad)

    # Assert
    assert result is None


# ---------------------------------------------------------------------------
# T4c — _warn_no_decrease warn / silent / no-existing
# ---------------------------------------------------------------------------


def test_warn_no_decrease_warns_when_count_stagnant(
    capsys: pytest.CaptureFixture,
) -> None:
    """Warns to stderr when new count >= old count and old count > 0."""
    # Arrange
    existing = {
        "counts": {
            "BLE001": {"DEBT": {"my-slug": 5}},
        }
    }
    new = {
        "counts": {
            "BLE001": {"DEBT": {"my-slug": 5}},  # same count — no decrease
        }
    }

    # Act
    _warn_no_decrease(new, existing)

    # Assert
    captured = capsys.readouterr()
    assert "WARN" in captured.err
    assert "BLE001" in captured.err
    assert "my-slug" in captured.err


def test_warn_no_decrease_silent_when_count_decreased(
    capsys: pytest.CaptureFixture,
) -> None:
    """Silent when new count < old count (improvement)."""
    # Arrange
    existing = {
        "counts": {
            "BLE001": {"DEBT": {"my-slug": 5}},
        }
    }
    new = {
        "counts": {
            "BLE001": {"DEBT": {"my-slug": 3}},  # decreased
        }
    }

    # Act
    _warn_no_decrease(new, existing)

    # Assert
    captured = capsys.readouterr()
    assert captured.err == ""


def test_warn_no_decrease_silent_when_no_existing(
    capsys: pytest.CaptureFixture,
) -> None:
    """Silent when existing is None (first run)."""
    # Arrange
    new = {
        "counts": {
            "BLE001": {"DEBT": {"my-slug": 5}},
        }
    }

    # Act
    _warn_no_decrease(new, None)

    # Assert
    captured = capsys.readouterr()
    assert captured.err == ""
