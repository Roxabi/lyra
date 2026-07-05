"""Tests for `factory.monitoring.log_watch.LogMonitorLoop` (issue #2245, plan task T6).

`LogMonitorLoop` is a thin V1-pull scheduler composing the two real, pure check
functions from `checks_log.py` (`check_nats_log_errors`,
`check_hub_dict_stream_gen_timeout`) — no NATS connection, no event/metric bus
producer (ADR-091 plane③ V1: pull, not V2: subscribe).

`TestRunOnce` exercises the real check functions through the real `LogFetcher`
seam via a fake fetcher that branches on the `pattern` argument — no mocking of
the module under test, no mocking of the check functions themselves.

`TestRunForever` breaks the `while True` loop deterministically by patching
`asyncio.sleep` with an `AsyncMock` whose `side_effect` raises a local sentinel
(`_StopLoop`) on its first call, letting exactly one `run_once()` iteration
(plus any alert dispatch) complete before the loop is forced to exit.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest

from factory.monitoring.config import MonitoringConfig
from factory.monitoring.log_watch import LogMonitorLoop

# ---------------------------------------------------------------------------
# Fake LogFetcher — branches on `pattern` to return controlled log text for
# each of the two checks without touching subprocess/httpx at all.
# ---------------------------------------------------------------------------


class _FakeFetcher:
    """Fake `LogFetcher` — routes by `pattern` so the real check functions
    (`check_nats_log_errors`, `check_hub_dict_stream_gen_timeout`) run for
    real against controlled input, exercising the actual counting/threshold
    logic in `checks_log.py`."""

    def __init__(self, nats_log: str = "", hub_log: str = "") -> None:
        self.nats_log = nats_log
        self.hub_log = hub_log

    def fetch(self, container_name: str, since_minutes: int, pattern: str) -> str:
        if pattern == "permissions violation":
            return self.nats_log
        if pattern == "_dict_stream_gen timeout":
            return self.hub_log
        raise AssertionError(f"unexpected pattern passed to fetcher: {pattern!r}")


class _StopLoop(Exception):
    """Local sentinel used to break `run_forever`'s `while True` loop
    deterministically after exactly one iteration, via the `asyncio.sleep`
    patch below."""


# ---------------------------------------------------------------------------
# run_once — aggregates both real checks into a HealthReport.
# ---------------------------------------------------------------------------


class TestRunOnce:
    async def test_run_once_all_pass(self) -> None:
        """Clean logs for both checks -> HealthReport reports zero failures."""
        config = MonitoringConfig()
        fetcher = _FakeFetcher(
            nats_log="all quiet\nnothing to see here\n",
            hub_log="all quiet\nnothing to see here\n",
        )
        loop = LogMonitorLoop(config, fetcher=fetcher)

        report = await loop.run_once()

        assert report.all_passed is True
        assert report.failed_count == 0
        assert len(report.checks) == 2

    async def test_run_once_one_check_fails(self) -> None:
        """Only the nats check trips -> failed_count=1, correct failing name."""
        config = MonitoringConfig()
        fetcher = _FakeFetcher(
            nats_log="permissions violation on subject foo\n",
            hub_log="all quiet\nnothing to see here\n",
        )
        loop = LogMonitorLoop(config, fetcher=fetcher)

        report = await loop.run_once()

        assert report.all_passed is False
        assert report.failed_count == 1
        failed = [c for c in report.checks if not c.passed]
        assert len(failed) == 1
        assert failed[0].name == "nats:permissions_violation"

    async def test_run_once_both_checks_fail(self) -> None:
        """Both checks trip -> failed_count=2.

        Default `stream_gen_timeout_threshold` is 3 (MonitoringConfig), so the
        hub log needs >=3 matching lines to fail per the real threshold logic
        in `check_hub_dict_stream_gen_timeout`.
        """
        config = MonitoringConfig()
        assert config.stream_gen_timeout_threshold == 3
        fetcher = _FakeFetcher(
            nats_log="permissions violation on subject foo\n",
            hub_log=(
                "_dict_stream_gen timeout on turn a\n"
                "_dict_stream_gen timeout on turn b\n"
                "_dict_stream_gen timeout on turn c\n"
            ),
        )
        loop = LogMonitorLoop(config, fetcher=fetcher)

        report = await loop.run_once()

        assert report.all_passed is False
        assert report.failed_count == 2


# ---------------------------------------------------------------------------
# run_forever — loops run_once() and alerts via escalation on failure.
#
# The `while True` loop is broken deterministically by patching
# `asyncio.sleep` to raise `_StopLoop` on its first call, after exactly one
# `run_once()` iteration (and any alert dispatch) has completed.
# ---------------------------------------------------------------------------


class TestRunForever:
    async def test_run_forever_all_pass_does_not_alert(self) -> None:
        """SC5: all checks pass -> zero Telegram alert calls."""
        config = MonitoringConfig()
        fetcher = _FakeFetcher(nats_log="all quiet\n", hub_log="all quiet\n")
        loop = LogMonitorLoop(config, fetcher=fetcher)

        with (
            patch(
                "factory.monitoring.log_watch.asyncio.sleep",
                new=AsyncMock(side_effect=_StopLoop),
            ),
            patch(
                "factory.monitoring.log_watch.escalation.send_telegram_raw_alert",
                new=AsyncMock(),
            ) as mock_alert,
        ):
            with pytest.raises(_StopLoop):
                await loop.run_forever()

        mock_alert.assert_not_called()

    async def test_run_forever_one_fail_alerts_once_with_both_results(self) -> None:
        """One check fails -> exactly one alert call, report carries both
        CheckResults (not just the failing one)."""
        config = MonitoringConfig()
        fetcher = _FakeFetcher(
            nats_log="permissions violation on subject foo\n",
            hub_log="all quiet\n",
        )
        loop = LogMonitorLoop(config, fetcher=fetcher)

        with (
            patch(
                "factory.monitoring.log_watch.asyncio.sleep",
                new=AsyncMock(side_effect=_StopLoop),
            ),
            patch(
                "factory.monitoring.log_watch.escalation.send_telegram_raw_alert",
                new=AsyncMock(),
            ) as mock_alert,
        ):
            with pytest.raises(_StopLoop):
                await loop.run_forever()

        mock_alert.assert_called_once()
        call_args = mock_alert.call_args
        report = call_args.args[0]
        assert len(report.checks) == 2
        assert report.all_passed is False
        assert report.failed_count == 1
        # send_telegram_raw_alert(report, config) — second positional arg is config.
        assert call_args.args[1] is config

    async def test_run_forever_both_fail_alerts_once_no_duplicates(self) -> None:
        """Both checks fail -> still exactly one alert call, not one per
        failing check — guards against per-check alert duplication."""
        config = MonitoringConfig()
        fetcher = _FakeFetcher(
            nats_log="permissions violation on subject foo\n",
            hub_log=(
                "_dict_stream_gen timeout on turn a\n"
                "_dict_stream_gen timeout on turn b\n"
                "_dict_stream_gen timeout on turn c\n"
            ),
        )
        loop = LogMonitorLoop(config, fetcher=fetcher)

        with (
            patch(
                "factory.monitoring.log_watch.asyncio.sleep",
                new=AsyncMock(side_effect=_StopLoop),
            ),
            patch(
                "factory.monitoring.log_watch.escalation.send_telegram_raw_alert",
                new=AsyncMock(),
            ) as mock_alert,
        ):
            with pytest.raises(_StopLoop):
                await loop.run_forever()

        mock_alert.assert_called_once()
        report = mock_alert.call_args.args[0]
        assert report.failed_count == 2

    async def test_run_forever_alert_delivery_failure_is_caught(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Alert dispatch raising a `_MONITORING_ESCALATION_ERRORS` member must
        be swallowed/logged, not propagate and crash the loop — the
        `_StopLoop` sentinel from the `asyncio.sleep` patch must still be the
        exception that surfaces."""
        config = MonitoringConfig()
        fetcher = _FakeFetcher(
            nats_log="permissions violation on subject foo\n",
            hub_log="all quiet\n",
        )
        loop = LogMonitorLoop(config, fetcher=fetcher)

        mock_alert = AsyncMock(side_effect=RuntimeError("telegram down"))

        with (
            patch(
                "factory.monitoring.log_watch.asyncio.sleep",
                new=AsyncMock(side_effect=_StopLoop),
            ),
            patch(
                "factory.monitoring.log_watch.escalation.send_telegram_raw_alert",
                new=mock_alert,
            ),
            caplog.at_level(logging.ERROR),
        ):
            # If the RuntimeError were NOT caught, it (not _StopLoop) would
            # propagate here and pytest.raises would report the wrong
            # exception type, failing the test.
            with pytest.raises(_StopLoop):
                await loop.run_forever()

        mock_alert.assert_called_once()
        assert any(
            "Telegram alert delivery failed" in record.message
            for record in caplog.records
        )

    async def test_run_forever_survives_unexpected_run_once_exception(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A single unexpected exception out of `run_once()` (e.g. a Loki
        response-shape bug not caught anywhere below) must be logged and
        skipped, not propagate and kill the loop — the `_StopLoop` sentinel
        from the `asyncio.sleep` patch must still be the exception that
        surfaces, proving the loop reached its next-tick sleep instead of
        dying on the `run_once()` call."""
        config = MonitoringConfig()
        loop = LogMonitorLoop(config, fetcher=_FakeFetcher())

        with (
            patch.object(
                loop, "run_once", new=AsyncMock(side_effect=RuntimeError("boom"))
            ),
            patch(
                "factory.monitoring.log_watch.asyncio.sleep",
                new=AsyncMock(side_effect=_StopLoop),
            ),
            patch(
                "factory.monitoring.log_watch.escalation.send_telegram_raw_alert",
                new=AsyncMock(),
            ) as mock_alert,
            caplog.at_level(logging.ERROR),
        ):
            # If the RuntimeError were NOT caught, it (not _StopLoop) would
            # propagate here and pytest.raises would report the wrong
            # exception type, failing the test.
            with pytest.raises(_StopLoop):
                await loop.run_forever()

        mock_alert.assert_not_called()
        assert any(
            "run_once() failed unexpectedly" in record.message
            for record in caplog.records
        )
