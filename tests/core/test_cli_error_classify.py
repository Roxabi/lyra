"""Tests for cli_error_classify.worker_error_from_cli_error."""

from __future__ import annotations

from factory.core.cli.cli_error_classify import worker_error_from_cli_error


class TestWorkerErrorFromCliError:
    def test_timeout_maps_to_transport_timeout(self) -> None:
        we = worker_error_from_cli_error("Timeout: no output for 120s")
        assert we.code == "transport.timeout"
        assert we.retryable is True

    def test_quota_message_maps_to_llm_rate_limit(self) -> None:
        we = worker_error_from_cli_error(
            "You've hit your weekly limit · resets 6pm (UTC)"
        )
        assert we.code == "llm.rate_limit"
        assert we.retryable is True
        assert "weekly limit" in we.message

    def test_auth_message_maps_to_cli_auth(self) -> None:
        we = worker_error_from_cli_error("Not logged in — run /login")
        assert we.code == "cli.auth"
        assert we.retryable is False

    def test_authenticated_does_not_map_to_cli_auth(self) -> None:
        we = worker_error_from_cli_error("User authenticated but request failed")
        assert we.code == "cli.parse"

    def test_unclassified_message_maps_to_cli_parse(self) -> None:
        we = worker_error_from_cli_error("unexpected CLI failure")
        assert we.code == "cli.parse"
        assert we.retryable is False
