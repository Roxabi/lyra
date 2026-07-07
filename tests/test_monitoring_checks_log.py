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

import re
import subprocess
from unittest.mock import MagicMock

import httpx
import pytest

from factory.monitoring.checks_log import (
    LogFetchError,
    LokiLogFetcher,
    SubprocessLogFetcher,
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
        stdout = "_dict_stream_gen timeout on turn abc\nunrelated line\n"
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

    def test_passes_at_threshold_minus_one_with_default_threshold(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Tight boundary at the realistic default threshold (3, per
        MonitoringConfig): exactly threshold-1 occurrences must still pass."""
        stdout = (
            "_dict_stream_gen timeout on turn a\n"
            "_dict_stream_gen timeout on turn b\n"
        )
        monkeypatch.setattr(
            "factory.monitoring.checks_log.subprocess.run",
            lambda *a, **kw: MagicMock(returncode=0, stdout=stdout, stderr=""),
        )

        result = check_hub_dict_stream_gen_timeout("factory-hub", 10, threshold=3)

        assert result.passed is True
        assert "2" in result.detail

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
            "_DICT_STREAM_GEN TIMEOUT on turn a\n_Dict_Stream_Gen Timeout on turn b\n"
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


# ---------------------------------------------------------------------------
# T4 (issue #2245, plan task T4) — tests against the POST-refactor
# LogFetcher seam. These import symbols (`LogFetchError`, `LokiLogFetcher`,
# `SubprocessLogFetcher`) that did not exist before this issue's T1-T3
# refactor, so — unlike T0 above — this is a genuine RED-GATE against the
# refactor itself, not just a characterization of pre-existing behavior.
# ---------------------------------------------------------------------------


class TestFetcherSeam:
    """Prove the injectable `fetcher` seam actually decouples log-fetch from
    the counting logic: an injected fetcher's `.fetch()` receives the right
    args, and `subprocess.run` is never touched when it is used."""

    def test_check_nats_log_errors_uses_injected_fetcher(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _fail_if_called(*args, **kwargs):
            raise AssertionError(
                "subprocess.run must not be called when a fetcher is injected"
            )

        monkeypatch.setattr(
            "factory.monitoring.checks_log.subprocess.run", _fail_if_called
        )

        fake_fetcher = MagicMock()
        fake_fetcher.fetch.return_value = "all quiet\n"

        result = check_nats_log_errors("factory-nats", 10, fetcher=fake_fetcher)

        fake_fetcher.fetch.assert_called_once_with(
            "factory-nats", 10, pattern="permissions violation"
        )
        assert result.passed is True

    def test_check_hub_dict_stream_gen_timeout_uses_injected_fetcher(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _fail_if_called(*args, **kwargs):
            raise AssertionError(
                "subprocess.run must not be called when a fetcher is injected"
            )

        monkeypatch.setattr(
            "factory.monitoring.checks_log.subprocess.run", _fail_if_called
        )

        fake_fetcher = MagicMock()
        fake_fetcher.fetch.return_value = ""

        result = check_hub_dict_stream_gen_timeout(
            "factory-hub", 10, threshold=1, fetcher=fake_fetcher
        )

        fake_fetcher.fetch.assert_called_once_with(
            "factory-hub", 10, pattern="_dict_stream_gen timeout"
        )
        assert result.passed is True


class TestLokiLogFetcher:
    """`LokiLogFetcher.fetch` talks to Loki via module-level `httpx.get`
    (not `httpx.Client`) — mock target is
    `factory.monitoring.checks_log.httpx.get`."""

    def test_fetch_returns_joined_log_lines_on_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "data": {
                "result": [
                    {
                        "values": [
                            [
                                "1700000000000000000",
                                "permissions violation on subject foo",
                            ],
                            [
                                "1700000001000000000",
                                "permissions violation on subject bar",
                            ],
                        ]
                    }
                ]
            }
        }
        mock_get = MagicMock(return_value=mock_response)
        monkeypatch.setattr("factory.monitoring.checks_log.httpx.get", mock_get)

        result = LokiLogFetcher().fetch(
            "factory-nats", 10, pattern="permissions violation"
        )

        assert result == (
            "permissions violation on subject foo\npermissions violation on subject bar"
        )

    def test_fetch_raises_log_fetch_error_on_malformed_json(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Missing expected keys (`data`/`result`/`values`) must surface as
        `LogFetchError`, not a raw KeyError/ValueError escaping the fetcher."""
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"unexpected": "shape"}
        mock_get = MagicMock(return_value=mock_response)
        monkeypatch.setattr("factory.monitoring.checks_log.httpx.get", mock_get)

        with pytest.raises(LogFetchError):
            LokiLogFetcher().fetch("factory-nats", 10, pattern="permissions violation")

    def test_fetch_raises_log_fetch_error_on_null_data_field(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`data["data"]` being `None` (a real Loki degraded-response shape)
        raises `TypeError` on subscript — must surface as `LogFetchError`,
        not escape the fetcher raw and kill the caller."""
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"data": None}
        mock_get = MagicMock(return_value=mock_response)
        monkeypatch.setattr("factory.monitoring.checks_log.httpx.get", mock_get)

        with pytest.raises(LogFetchError):
            LokiLogFetcher().fetch("factory-nats", 10, pattern="permissions violation")

    def test_fetch_raises_log_fetch_error_on_short_values_entry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A `values` entry with fewer than 2 elements raises `IndexError` on
        `value[1]` — must surface as `LogFetchError`, not escape the fetcher
        raw and kill the caller."""
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "data": {"result": [{"values": [["1700000000000000000"]]}]}
        }
        mock_get = MagicMock(return_value=mock_response)
        monkeypatch.setattr("factory.monitoring.checks_log.httpx.get", mock_get)

        with pytest.raises(LogFetchError):
            LokiLogFetcher().fetch("factory-nats", 10, pattern="permissions violation")

    def test_fetch_raises_log_fetch_error_on_http_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500 Internal Server Error",
            request=MagicMock(),
            response=MagicMock(),
        )
        mock_get = MagicMock(return_value=mock_response)
        monkeypatch.setattr("factory.monitoring.checks_log.httpx.get", mock_get)

        with pytest.raises(LogFetchError):
            LokiLogFetcher().fetch("factory-nats", 10, pattern="permissions violation")

    @pytest.mark.parametrize(
        "pattern",
        [
            "permissions violation",
            "_dict_stream_gen timeout",
        ],
    )
    def test_fetch_query_param_includes_case_insensitive_regex_filter(
        self, monkeypatch: pytest.MonkeyPatch, pattern: str
    ) -> None:
        """Regression guard for the truncation-risk fix: the server-side
        `|~ "(?i)<escaped-pattern>"` LogQL filter must survive future edits.
        Without it, Loki's `limit`-capped, newest-first `query_range`
        response on a busy container could silently drop true violations
        under load (see checks_log.py's comment on LokiLogFetcher.fetch)."""
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"data": {"result": []}}
        mock_get = MagicMock(return_value=mock_response)
        monkeypatch.setattr("factory.monitoring.checks_log.httpx.get", mock_get)

        LokiLogFetcher().fetch("factory-nats", 10, pattern=pattern)

        mock_get.assert_called_once()
        _, kwargs = mock_get.call_args
        query = kwargs["params"]["query"]
        escaped = re.escape(pattern).replace(r"\ ", " ")
        assert f'|~ "(?i){escaped}"' in query

    def test_fetch_returns_empty_string_on_zero_matching_streams(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A well-formed response with zero matching streams (`"result": []`)
        must return `""` directly — not raise, not `None`, exact empty
        string, so `splitlines()` on the caller side sees zero lines."""
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"data": {"result": []}}
        mock_get = MagicMock(return_value=mock_response)
        monkeypatch.setattr("factory.monitoring.checks_log.httpx.get", mock_get)

        result = LokiLogFetcher().fetch(
            "factory-nats", 10, pattern="permissions violation"
        )

        assert result == ""

    def test_fetch_query_params_include_limit_and_time_window(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`limit` is called out in checks_log.py's own comment as
        load-bearing (Loki caps + truncates newest-first without it); `start`/
        `end` must bound the query to the requested `since_minutes` window."""
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"data": {"result": []}}
        mock_get = MagicMock(return_value=mock_response)
        monkeypatch.setattr("factory.monitoring.checks_log.httpx.get", mock_get)

        LokiLogFetcher().fetch("factory-nats", 10, pattern="permissions violation")

        mock_get.assert_called_once()
        _, kwargs = mock_get.call_args
        params = kwargs["params"]
        assert params["limit"] == 5000
        assert isinstance(params["start"], int)
        assert isinstance(params["end"], int)
        assert params["end"] > params["start"]
        # 10 minutes, in nanoseconds.
        assert params["end"] - params["start"] == 10 * 60 * 1_000_000_000


class TestSubprocessLogFetcherIgnoresPattern:
    """`pattern` is accepted for `LogFetcher` protocol conformance but has
    no effect on the local subprocess fetch — a deliberate design choice
    (podman's `--since` has no line-count cap, so there is no truncation
    risk to filter against locally), not an oversight."""

    def test_fetch_ignores_pattern_argument(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[tuple[tuple, dict]] = []

        def mock_run(*args, **kwargs):
            calls.append((args, kwargs))
            return MagicMock(
                returncode=0, stdout="stdout content\n", stderr="stderr content\n"
            )

        monkeypatch.setattr("factory.monitoring.checks_log.subprocess.run", mock_run)

        result_a = SubprocessLogFetcher().fetch("c", 5, pattern="anything")
        result_b = SubprocessLogFetcher().fetch("c", 5, pattern="permissions violation")

        assert result_a == "stdout content\nstderr content\n"
        assert result_b == "stdout content\nstderr content\n"
        # The command passed to subprocess.run must be identical regardless
        # of `pattern` — proving it is truly not threaded into the command.
        assert len(calls) == 2
        args_a, _ = calls[0]
        args_b, _ = calls[1]
        assert args_a[0] == args_b[0] == ["podman", "logs", "--since", "5m", "c"]
        assert "anything" not in args_a[0]
        assert "permissions violation" not in args_b[0]
