"""Bootstrap wiring tests for _bootstrap_log_monitor_standalone (#2245).

Covers the FACTORY_LOG_MONITOR_ONCE single-tick mechanism (once-mode exits
with the report's pass/fail status; forever-mode never calls sys.exit) and a
static SC10 guard: this V1-pull slice must never grow a V2 event/metric-bus
publish/subscribe surface (that's #1035, explicitly out of scope).

Patch targets: load_monitoring_config and LogMonitorLoop are imported via
function-local `from ... import ...` statements inside
_bootstrap_log_monitor_standalone, so they are never bound as module-level
attributes of worker_standalone (confirmed: `dir(worker_standalone)` does not
contain either name). Patching
`factory.bootstrap.standalone.worker_standalone.load_monitoring_config`
would raise AttributeError at patch-setup time. The deferred import re-reads
the name from its *defining* module at call time, so the correct patch
targets are the source modules: `factory.monitoring.config.load_monitoring_config`
and `factory.monitoring.log_watch.LogMonitorLoop`.
"""

from __future__ import annotations

import importlib
import inspect
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from factory.monitoring.models import HealthReport


def _make_report(*, all_passed: bool, failed_count: int) -> HealthReport:
    return HealthReport(
        checks=[],
        all_passed=all_passed,
        failed_count=failed_count,
        timestamp=datetime.now(timezone.utc),
    )


class TestLogMonitorOnceMode:
    @pytest.mark.asyncio
    async def test_once_mode_all_pass_exits_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FACTORY_LOG_MONITOR_ONCE set + all checks pass → SystemExit(0)."""
        from factory.bootstrap.standalone.worker_standalone import (
            _bootstrap_log_monitor_standalone,
        )

        monkeypatch.setenv("FACTORY_LOG_MONITOR_ONCE", "1")

        report = _make_report(all_passed=True, failed_count=0)
        mock_loop = MagicMock()
        mock_loop.run_once = AsyncMock(return_value=report)
        mock_loop.run_forever = AsyncMock()

        with (
            patch(
                "factory.monitoring.config.load_monitoring_config",
                return_value=MagicMock(check_interval_minutes=5),
            ),
            patch(
                "factory.monitoring.log_watch.LogMonitorLoop",
                return_value=mock_loop,
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            await _bootstrap_log_monitor_standalone({})

        assert exc_info.value.code == 0
        mock_loop.run_once.assert_awaited_once()
        mock_loop.run_forever.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_once_mode_failure_exits_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FACTORY_LOG_MONITOR_ONCE set + a check fails → SystemExit(1)."""
        from factory.bootstrap.standalone.worker_standalone import (
            _bootstrap_log_monitor_standalone,
        )

        monkeypatch.setenv("FACTORY_LOG_MONITOR_ONCE", "1")

        report = _make_report(all_passed=False, failed_count=1)
        mock_loop = MagicMock()
        mock_loop.run_once = AsyncMock(return_value=report)
        mock_loop.run_forever = AsyncMock()

        with (
            patch(
                "factory.monitoring.config.load_monitoring_config",
                return_value=MagicMock(check_interval_minutes=5),
            ),
            patch(
                "factory.monitoring.log_watch.LogMonitorLoop",
                return_value=mock_loop,
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            await _bootstrap_log_monitor_standalone({})

        assert exc_info.value.code == 1
        mock_loop.run_once.assert_awaited_once()
        mock_loop.run_forever.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_once_mode_never_calls_run_forever(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Once-mode never touches run_forever, regardless of pass/fail outcome."""
        from factory.bootstrap.standalone.worker_standalone import (
            _bootstrap_log_monitor_standalone,
        )

        monkeypatch.setenv("FACTORY_LOG_MONITOR_ONCE", "1")

        report = _make_report(all_passed=True, failed_count=0)
        mock_loop = MagicMock()
        mock_loop.run_once = AsyncMock(return_value=report)
        mock_loop.run_forever = AsyncMock()

        with (
            patch(
                "factory.monitoring.config.load_monitoring_config",
                return_value=MagicMock(check_interval_minutes=5),
            ),
            patch(
                "factory.monitoring.log_watch.LogMonitorLoop",
                return_value=mock_loop,
            ),
            pytest.raises(SystemExit),
        ):
            await _bootstrap_log_monitor_standalone({})

        mock_loop.run_forever.assert_not_awaited()


class TestLogMonitorForeverMode:
    @pytest.mark.asyncio
    async def test_no_once_env_calls_run_forever_not_sys_exit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FACTORY_LOG_MONITOR_ONCE unset → run_forever awaited, no SystemExit."""
        from factory.bootstrap.standalone.worker_standalone import (
            _bootstrap_log_monitor_standalone,
        )

        monkeypatch.delenv("FACTORY_LOG_MONITOR_ONCE", raising=False)

        mock_loop = MagicMock()
        mock_loop.run_once = AsyncMock()
        # Simulate a clean shutdown: run_forever returns immediately.
        mock_loop.run_forever = AsyncMock(return_value=None)

        with (
            patch(
                "factory.monitoring.config.load_monitoring_config",
                return_value=MagicMock(check_interval_minutes=5),
            ),
            patch(
                "factory.monitoring.log_watch.LogMonitorLoop",
                return_value=mock_loop,
            ),
        ):
            # Should return normally — no SystemExit raised in forever-mode.
            await _bootstrap_log_monitor_standalone({})

        mock_loop.run_forever.assert_awaited_once()
        mock_loop.run_once.assert_not_called()


class TestNoEventMetricSubjects:
    """SC10 static guard: this V1-pull slice must never grow a V2 event/metric-bus
    publish/subscribe surface (that's #1035, explicitly out of scope).

    Pure source inspection — no mocking, no async. Scoped to the exact
    functions/module touched by this slice (not whole shared files) to avoid
    false positives from unrelated code sharing a file.
    """

    def test_log_watch_module_has_no_event_or_metric_subjects(self) -> None:
        import factory.monitoring.log_watch as log_watch

        source = inspect.getsource(log_watch)
        assert "factory.event." not in source
        assert "factory.metric." not in source

    def test_bootstrap_log_monitor_standalone_has_no_event_or_metric_subjects(
        self,
    ) -> None:
        from factory.bootstrap.standalone.worker_standalone import (
            _bootstrap_log_monitor_standalone,
        )

        source = inspect.getsource(_bootstrap_log_monitor_standalone)
        assert "factory.event." not in source
        assert "factory.metric." not in source

    def test_cli_log_monitor_command_has_no_event_or_metric_subjects(self) -> None:
        # factory.cli.__init__ re-exports `main` (from factory.cli.main import
        # main), which shadows the `main` submodule attribute on the `factory.cli`
        # package. `import factory.cli.main as m` would therefore resolve `m` to
        # the *function*, not the module. Go through sys.modules via
        # importlib.import_module to get the real module unambiguously.
        cli_main = importlib.import_module("factory.cli.main")
        log_monitor_command = cli_main._log_monitor

        source = inspect.getsource(log_monitor_command)
        assert "factory.event." not in source
        assert "factory.metric." not in source
