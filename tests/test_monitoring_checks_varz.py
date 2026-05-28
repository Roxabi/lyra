"""Tests for checks_varz disk_pct and inode_pct primitives."""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

from lyra.monitoring.checks_varz import check_disk_pct, check_inode_pct
from lyra.monitoring.models import CheckResult

# ---------------------------------------------------------------------------
# check_disk_pct
# ---------------------------------------------------------------------------


class TestCheckDiskPct:
    def test_returns_check_result(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """check_disk_pct returns a CheckResult with name='disk_pct'."""
        monkeypatch.setattr(
            "lyra.monitoring.checks_varz.shutil.disk_usage",
            lambda _: MagicMock(total=100, used=30, free=70),
        )
        result = check_disk_pct("/tmp", warning_pct=60, critical_pct=70)
        assert isinstance(result, CheckResult)
        assert result.name == "disk_pct"
        assert isinstance(result.passed, bool)
        assert isinstance(result.detail, str)

    def test_below_warning_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Usage 30%% < warning 60%% → passed=True, level=OK."""
        monkeypatch.setattr(
            "lyra.monitoring.checks_varz.shutil.disk_usage",
            lambda _: MagicMock(total=100, used=30, free=70),
        )
        result = check_disk_pct("/tmp", warning_pct=60, critical_pct=70)
        assert result.passed is True
        assert "OK" in result.detail
        assert "used=30.0%" in result.detail

    def test_at_warning_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Usage exactly at warning threshold → passed=False, level=WARNING."""
        monkeypatch.setattr(
            "lyra.monitoring.checks_varz.shutil.disk_usage",
            lambda _: MagicMock(total=100, used=60, free=40),
        )
        result = check_disk_pct("/tmp", warning_pct=60, critical_pct=70)
        assert result.passed is False
        assert "WARNING" in result.detail
        assert "used=60.0%" in result.detail

    def test_between_warning_and_critical(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Usage 65%% between warning 60%% and critical 70%% → WARNING, passed=False."""
        monkeypatch.setattr(
            "lyra.monitoring.checks_varz.shutil.disk_usage",
            lambda _: MagicMock(total=100, used=65, free=35),
        )
        result = check_disk_pct("/tmp", warning_pct=60, critical_pct=70)
        assert result.passed is False
        assert "WARNING" in result.detail
        assert "used=65.0%" in result.detail

    def test_at_critical_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Usage exactly at critical threshold → passed=False, level=CRITICAL."""
        monkeypatch.setattr(
            "lyra.monitoring.checks_varz.shutil.disk_usage",
            lambda _: MagicMock(total=100, used=70, free=30),
        )
        result = check_disk_pct("/tmp", warning_pct=60, critical_pct=70)
        assert result.passed is False
        assert "CRITICAL" in result.detail
        assert "used=70.0%" in result.detail

    def test_above_critical(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Usage 90%% > critical 70%% → CRITICAL, passed=False."""
        monkeypatch.setattr(
            "lyra.monitoring.checks_varz.shutil.disk_usage",
            lambda _: MagicMock(total=100, used=90, free=10),
        )
        result = check_disk_pct("/tmp", warning_pct=60, critical_pct=70)
        assert result.passed is False
        assert "CRITICAL" in result.detail
        assert "used=90.0%" in result.detail

    def test_zero_total(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Zero total disk space → used_pct=0, passed=True (no crash)."""
        monkeypatch.setattr(
            "lyra.monitoring.checks_varz.shutil.disk_usage",
            lambda _: MagicMock(total=0, used=0, free=0),
        )
        result = check_disk_pct("/tmp", warning_pct=60, critical_pct=70)
        assert result.passed is True
        assert "OK" in result.detail
        assert "used=0.0%" in result.detail


# ---------------------------------------------------------------------------
# check_inode_pct
# ---------------------------------------------------------------------------


class TestCheckInodePct:
    def _mock_statvfs(self, f_files: int, f_ffree: int) -> os.statvfs_result:
        """Build a statvfs result with the given inode totals."""
        return os.statvfs_result(
            (
                4096,       # f_bsize
                4096,       # f_frsize
                1000000,    # f_blocks
                500000,     # f_bfree
                500000,     # f_bavail
                f_files,    # f_files
                f_ffree,    # f_ffree
                f_ffree,    # f_favail
                0,          # f_flag
                255,        # f_namemax
            )
        )

    def test_returns_check_result(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """check_inode_pct returns a CheckResult with name='inode_pct'."""
        monkeypatch.setattr(
            "lyra.monitoring.checks_varz.os.statvfs",
            lambda _: self._mock_statvfs(f_files=1000, f_ffree=700),
        )
        result = check_inode_pct("/tmp", warning_pct=60, critical_pct=70)
        assert isinstance(result, CheckResult)
        assert result.name == "inode_pct"
        assert isinstance(result.passed, bool)
        assert isinstance(result.detail, str)

    def test_below_warning_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Inode usage 30%% < warning 60%% → passed=True, level=OK."""
        monkeypatch.setattr(
            "lyra.monitoring.checks_varz.os.statvfs",
            lambda _: self._mock_statvfs(f_files=1000, f_ffree=700),
        )
        result = check_inode_pct("/tmp", warning_pct=60, critical_pct=70)
        assert result.passed is True
        assert "OK" in result.detail
        assert "used=30.0%" in result.detail

    def test_at_warning_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Inode usage exactly at warning threshold → passed=False, level=WARNING."""
        monkeypatch.setattr(
            "lyra.monitoring.checks_varz.os.statvfs",
            lambda _: self._mock_statvfs(f_files=1000, f_ffree=400),
        )
        result = check_inode_pct("/tmp", warning_pct=60, critical_pct=70)
        assert result.passed is False
        assert "WARNING" in result.detail
        assert "used=60.0%" in result.detail

    def test_between_warning_and_critical(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Inode usage 65%% between warning 60%% and critical 70%% → WARNING."""
        monkeypatch.setattr(
            "lyra.monitoring.checks_varz.os.statvfs",
            lambda _: self._mock_statvfs(f_files=1000, f_ffree=350),
        )
        result = check_inode_pct("/tmp", warning_pct=60, critical_pct=70)
        assert result.passed is False
        assert "WARNING" in result.detail
        assert "used=65.0%" in result.detail

    def test_at_critical_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Inode usage exactly at critical threshold → passed=False, level=CRITICAL."""
        monkeypatch.setattr(
            "lyra.monitoring.checks_varz.os.statvfs",
            lambda _: self._mock_statvfs(f_files=1000, f_ffree=300),
        )
        result = check_inode_pct("/tmp", warning_pct=60, critical_pct=70)
        assert result.passed is False
        assert "CRITICAL" in result.detail
        assert "used=70.0%" in result.detail

    def test_above_critical(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Inode usage 90%% > critical 70%% → CRITICAL, passed=False."""
        monkeypatch.setattr(
            "lyra.monitoring.checks_varz.os.statvfs",
            lambda _: self._mock_statvfs(f_files=1000, f_ffree=100),
        )
        result = check_inode_pct("/tmp", warning_pct=60, critical_pct=70)
        assert result.passed is False
        assert "CRITICAL" in result.detail
        assert "used=90.0%" in result.detail

    def test_zero_total(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Zero total inodes → used_pct=0, passed=True (no crash)."""
        monkeypatch.setattr(
            "lyra.monitoring.checks_varz.os.statvfs",
            lambda _: self._mock_statvfs(f_files=0, f_ffree=0),
        )
        result = check_inode_pct("/tmp", warning_pct=60, critical_pct=70)
        assert result.passed is True
        assert "OK" in result.detail
        assert "used=0.0%" in result.detail

    def test_detail_includes_thresholds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Detail string includes both warning and critical percentages."""
        monkeypatch.setattr(
            "lyra.monitoring.checks_varz.os.statvfs",
            lambda _: self._mock_statvfs(f_files=100, f_ffree=50),
        )
        result = check_inode_pct("/tmp", warning_pct=60, critical_pct=70)
        assert "warning=60%" in result.detail
        assert "critical=70%" in result.detail
