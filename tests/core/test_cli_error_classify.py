"""Tests for cli_error_classify.worker_error_from_cli_error."""

from __future__ import annotations

from factory.core.cli.cli_error_classify import worker_error_from_cli_error


class TestWorkerErrorFromCliError:
    def test_timeout_maps_to_transport_timeout(self) -> None:
        we = worker_error_from_cli_error("Timeout: no output for 120s")
        assert we.code == "transport.timeout"
        assert we.retryable is True

    def test_quota_message_maps_to_cli_parse(self) -> None:
        we = worker_error_from_cli_error(
            "You've hit your weekly limit · resets 6pm (UTC)"
        )
        assert we.code == "cli.parse"
        assert "weekly limit" in we.message