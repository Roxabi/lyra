"""Tests for Layer 1 monitoring primitive checks:
process, disk, circuits, queue depth."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# check_process
# ---------------------------------------------------------------------------


class TestCheckProcess:
    def test_active_supervisor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """check_process passes for RUNNING supervisor process."""
        monkeypatch.setattr(
            "lyra.monitoring.checks.subprocess.run",
            lambda *a, **kw: MagicMock(
                returncode=0,
                stdout="lyra-telegram                    RUNNING   pid 1234, uptime 1:00:00\n",  # noqa: E501
            ),
        )
        from lyra.monitoring.checks import check_process

        result = check_process("lyra-telegram")
        assert result.passed is True
        assert result.name == "process"
        assert "RUNNING" in result.detail

    def test_inactive_supervisor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """check_process fails for STOPPED supervisor process."""
        monkeypatch.setattr(
            "lyra.monitoring.checks.subprocess.run",
            lambda *a, **kw: MagicMock(
                returncode=3,
                stdout="lyra-telegram                    STOPPED   Mar 30 12:00 PM\n",
            ),
        )
        from lyra.monitoring.checks import check_process

        result = check_process("lyra-telegram")
        assert result.passed is False

    def test_fallback_to_systemctl(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """check_process falls back to systemctl when supervisorctl not found."""
        from unittest.mock import patch

        monkeypatch.setattr(
            "lyra.monitoring.checks.subprocess.run",
            lambda *a, **kw: MagicMock(returncode=0, stdout="active\n"),
        )
        from lyra.monitoring.checks import check_process

        with patch("lyra.monitoring.checks.Path.exists", return_value=False):
            result = check_process("lyra")
        assert result.passed is True


# ---------------------------------------------------------------------------
# check_queue_depth
# ---------------------------------------------------------------------------


class TestCheckQueueDepth:
    def test_below_threshold(self) -> None:
        """SC-4: check_queue_depth passes when queue_size < threshold."""
        from lyra.monitoring.checks import check_queue_depth

        result = check_queue_depth({"queue_size": 10}, 80)
        assert result.passed is True
        assert result.name == "queue_depth"

    def test_above_threshold(self) -> None:
        """SC-4: check_queue_depth fails when queue_size >= threshold."""
        from lyra.monitoring.checks import check_queue_depth

        result = check_queue_depth({"queue_size": 90}, 80)
        assert result.passed is False

    def test_at_exact_threshold(self) -> None:
        """Boundary: queue_size == threshold should fail (uses strict <)."""
        from lyra.monitoring.checks import check_queue_depth

        result = check_queue_depth({"queue_size": 80}, 80)
        assert result.passed is False


# ---------------------------------------------------------------------------
# check_circuits
# ---------------------------------------------------------------------------


class TestCheckCircuits:
    def test_all_closed(self) -> None:
        """SC-4: check_circuits passes when all circuits are closed."""
        from lyra.monitoring.checks import check_circuits

        health_json = {
            "circuits": {
                "claude-cli": {"state": "closed"},
                "telegram": {"state": "closed"},
            }
        }
        result = check_circuits(health_json)
        assert result.passed is True
        assert result.name == "circuits"

    def test_open_circuit(self) -> None:
        """SC-4: check_circuits fails when any circuit is open."""
        from lyra.monitoring.checks import check_circuits

        health_json = {
            "circuits": {
                "claude-cli": {"state": "open"},
                "telegram": {"state": "closed"},
            }
        }
        result = check_circuits(health_json)
        assert result.passed is False


# ---------------------------------------------------------------------------
# check_disk
# ---------------------------------------------------------------------------


class TestCheckDisk:
    def test_sufficient_space(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SC-4: check_disk passes when free space is above threshold."""
        import shutil

        monkeypatch.setattr(
            "lyra.monitoring.checks.shutil.disk_usage",
            lambda path: shutil._ntuple_diskusage(
                total=100 * 1024**3, used=50 * 1024**3, free=50 * 1024**3
            ),
        )
        from lyra.monitoring.checks import check_disk

        result = check_disk("/", 1)
        assert result.passed is True
        assert result.name == "disk"

    def test_insufficient_space(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SC-4: check_disk fails when free space is below threshold."""
        import shutil

        monkeypatch.setattr(
            "lyra.monitoring.checks.shutil.disk_usage",
            lambda path: shutil._ntuple_diskusage(
                total=100 * 1024**3,
                used=int(99.5 * 1024**3),
                free=int(0.5 * 1024**3),
            ),
        )
        from lyra.monitoring.checks import check_disk

        result = check_disk("/", 1)
        assert result.passed is False
