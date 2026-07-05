"""Baseline characterization tests for `factory.monitoring.checks_log`.

Written against the CURRENT, UNMODIFIED source (issue #2245, plan task T0) — no
`fetcher`/`LogFetcher` seam exists yet. This file locks in today's exact
behavior of `check_nats_log_errors` and `check_hub_dict_stream_gen_timeout`
BEFORE a later refactor (T1-T3) introduces an injectable log-fetch seam, so a
behavior regression during that refactor shows up as a red test here
(SC3's falsification, per the plan).

Do not modify `checks_log.py` from this file — mocking only, never `vi.mock`-
style replacement of the module under test.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock

import pytest

from factory.monitoring.checks_log import (
    check_hub_dict_stream_gen_timeout,
    check_nats_log_errors,
)

# ---------------------------------------------------------------------------
# check_nats_log_errors — case-SENSITIVE substring count of
# "permissions violation"; count > 0 -> failed.
# ---------------------------------------------------------------------------


class TestCheckNatsLogErrors:
    def test_passes_with_zero_occurrences(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """0 occurrences of the exact-case substring -> passed."""
        monkeypatch.setattr(
            "factory.monitoring.checks_log.subprocess.run",
            lambda *a, **kw: MagicMock(
                returncode=0, stdout="all quiet\nnothing to see\n", stderr=""
            ),
        )

        result = check_nats_log_errors("factory-nats", 10)

        assert result.passed is True
        assert result.name == "nats:permissions_violation"
        assert "0 violations" in result.detail

    def test_fails_with_correct_count_on_occurrences(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """>0 occurrences -> failed, with the exact count reflected in detail."""
        stdout = (
            "line one ok\n"
            "permissions violation on subject foo\n"
            "line three ok\n"
            "permissions violation on subject bar\n"
        )
        monkeypatch.setattr(
            "factory.monitoring.checks_log.subprocess.run",
            lambda *a, **kw: MagicMock(returncode=0, stdout=stdout, stderr=""),
        )

        result = check_nats_log_errors("factory-nats", 10)

        assert result.passed is False
        assert result.name == "nats:permissions_violation"
        assert "2" in result.detail

    def test_case_sensitive_near_misses_not_counted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Mixed-case near-misses must NOT be counted — check is case-sensitive."""
        stdout = (
            "Permissions Violation on subject foo\n"
            "PERMISSIONS VIOLATION on subject bar\n"
            "permissions Violation on subject baz\n"
        )
        monkeypatch.setattr(
            "factory.monitoring.checks_log.subprocess.run",
            lambda *a, **kw: MagicMock(returncode=0, stdout=stdout, stderr=""),
        )

        result = check_nats_log_errors("factory-nats", 10)

        # None of the near-misses match the exact-case substring.
        assert result.passed is True
        assert "0 violations" in result.detail

    def test_reads_both_stdout_and_stderr(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Occurrences in stderr must be counted too (stdout + stderr combined)."""
        monkeypatch.setattr(
            "factory.monitoring.checks_log.subprocess.run",
            lambda *a, **kw: MagicMock(
                returncode=0,
                stdout="line ok\n",
                stderr="permissions violation on subject foo\n",
            ),
        )

        result = check_nats_log_errors("factory-nats", 10)

        assert result.passed is False
        assert "1" in result.detail

    def test_name_is_stable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "factory.monitoring.checks_log.subprocess.run",
            lambda *a, **kw: MagicMock(returncode=0, stdout="", stderr=""),
        )

        result = check_nats_log_errors("factory-nats", 10)

        assert result.name == "nats:permissions_violation"


# ---------------------------------------------------------------------------
# check_hub_dict_stream_gen_timeout — case-INSENSITIVE substring count of
# "_dict_stream_gen timeout"; count >= threshold -> failed.
# ---------------------------------------------------------------------------


class TestCheckHubDictStreamGenTimeout:
    def test_passes_when_count_below_threshold(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stdout = "_dict_stream_gen timeout on turn abc\n" "unrelated line\n"
        monkeypatch.setattr(
            "factory.monitoring.checks_log.subprocess.run",
            lambda *a, **kw: MagicMock(returncode=0, stdout=stdout, stderr=""),
        )

        result = check_hub_dict_stream_gen_timeout("factory-hub", 10, threshold=3)

        assert result.passed is True
        assert result.name == "hub:dict_stream_gen_timeout"

    def test_fails_when_count_meets_threshold(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stdout = (
            "_dict_stream_gen timeout on turn a\n"
            "_dict_stream_gen timeout on turn b\n"
            "_dict_stream_gen timeout on turn c\n"
        )
        monkeypatch.setattr(
            "factory.monitoring.checks_log.subprocess.run",
            lambda *a, **kw: MagicMock(returncode=0, stdout=stdout, stderr=""),
        )

        result = check_hub_dict_stream_gen_timeout("factory-hub", 10, threshold=3)

        assert result.passed is False
        assert result.name == "hub:dict_stream_gen_timeout"
        assert "3" in result.detail
        assert "threshold=3" in result.detail

    def test_fails_when_count_exceeds_threshold(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stdout = "_dict_stream_gen timeout\n" * 5
        monkeypatch.setattr(
            "factory.monitoring.checks_log.subprocess.run",
            lambda *a, **kw: MagicMock(returncode=0, stdout=stdout, stderr=""),
        )

        result = check_hub_dict_stream_gen_timeout("factory-hub", 10, threshold=3)

        assert result.passed is False
        assert "5" in result.detail

    def test_case_insensitive_matching_is_counted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Upper/mixed-case occurrences ARE counted — check is case-insensitive."""
        stdout = (
            "_DICT_STREAM_GEN TIMEOUT on turn a\n"
            "_Dict_Stream_Gen Timeout on turn b\n"
        )
        monkeypatch.setattr(
            "factory.monitoring.checks_log.subprocess.run",
            lambda *a, **kw: MagicMock(returncode=0, stdout=stdout, stderr=""),
        )

        result = check_hub_dict_stream_gen_timeout("factory-hub", 10, threshold=2)

        assert result.passed is False
        assert "2" in result.detail

    def test_name_is_stable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "factory.monitoring.checks_log.subprocess.run",
            lambda *a, **kw: MagicMock(returncode=0, stdout="", stderr=""),
        )

        result = check_hub_dict_stream_gen_timeout("factory-hub", 10, threshold=1)

        assert result.name == "hub:dict_stream_gen_timeout"


# ---------------------------------------------------------------------------
# Exception handling — both functions catch
# (TimeoutExpired, FileNotFoundError, CalledProcessError) and return a
# failing CheckResult with detail=str(exc).
# ---------------------------------------------------------------------------

_EXCEPTION_CASES = [
    pytest.param(
        subprocess.TimeoutExpired(cmd=["podman", "logs"], timeout=15),
        id="timeout-expired",
    ),
    pytest.param(
        FileNotFoundError("podman: command not found"),
        id="file-not-found",
    ),
    pytest.param(
        subprocess.CalledProcessError(returncode=1, cmd=["podman", "logs"]),
        id="called-process-error",
    ),
]


class TestLogCheckExceptionHandling:
    @pytest.mark.parametrize("exc", _EXCEPTION_CASES)
    def test_check_nats_log_errors_returns_failing_result(
        self, monkeypatch: pytest.MonkeyPatch, exc: Exception
    ) -> None:
        def mock_run(*args, **kwargs):
            raise exc

        monkeypatch.setattr("factory.monitoring.checks_log.subprocess.run", mock_run)

        result = check_nats_log_errors("factory-nats", 10)

        assert result.passed is False
        assert result.name == "nats:permissions_violation"
        assert result.detail == str(exc)

    @pytest.mark.parametrize("exc", _EXCEPTION_CASES)
    def test_check_hub_dict_stream_gen_timeout_returns_failing_result(
        self, monkeypatch: pytest.MonkeyPatch, exc: Exception
    ) -> None:
        def mock_run(*args, **kwargs):
            raise exc

        monkeypatch.setattr("factory.monitoring.checks_log.subprocess.run", mock_run)

        result = check_hub_dict_stream_gen_timeout("factory-hub", 10, threshold=1)

        assert result.passed is False
        assert result.name == "hub:dict_stream_gen_timeout"
        assert result.detail == str(exc)


# ---------------------------------------------------------------------------
# Regression guard — exact subprocess invocation shape. A later refactor
# (injectable LogFetcher seam) must not silently change these args without
# failing this test.
# ---------------------------------------------------------------------------


class TestSubprocessInvocationShape:
    def test_check_nats_log_errors_invokes_podman_logs_exactly(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[tuple[tuple, dict]] = []

        def mock_run(*args, **kwargs):
            calls.append((args, kwargs))
            return MagicMock(returncode=0, stdout="", stderr="")

        monkeypatch.setattr("factory.monitoring.checks_log.subprocess.run", mock_run)

        check_nats_log_errors("factory-nats", 10)

        assert len(calls) == 1
        args, kwargs = calls[0]
        assert args[0] == ["podman", "logs", "--since", "10m", "factory-nats"]
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["timeout"] == 15

    def test_check_hub_dict_stream_gen_timeout_invokes_podman_logs_exactly(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[tuple[tuple, dict]] = []

        def mock_run(*args, **kwargs):
            calls.append((args, kwargs))
            return MagicMock(returncode=0, stdout="", stderr="")

        monkeypatch.setattr("factory.monitoring.checks_log.subprocess.run", mock_run)

        check_hub_dict_stream_gen_timeout("factory-hub", 7, threshold=2)

        assert len(calls) == 1
        args, kwargs = calls[0]
        assert args[0] == ["podman", "logs", "--since", "7m", "factory-hub"]
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["timeout"] == 15
