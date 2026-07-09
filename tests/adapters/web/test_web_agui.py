"""Unit tests for AG-UI wire helpers."""

from __future__ import annotations

from factory.adapters.web.web_agui import is_stream_terminal


class TestIsStreamTerminal:
    def test_agui_run_finished(self) -> None:
        assert is_stream_terminal({"type": "RUN_FINISHED"}, "agui") is True

    def test_agui_run_error(self) -> None:
        assert is_stream_terminal({"type": "RUN_ERROR"}, "agui") is True

    def test_agui_delta_not_terminal(self) -> None:
        assert (
            is_stream_terminal(
                {"type": "TEXT_MESSAGE_CONTENT", "messageId": "m1", "delta": "hi"},
                "agui",
            )
            is False
        )

    def test_legacy_done(self) -> None:
        assert is_stream_terminal({"type": "done"}, "legacy") is True

    def test_legacy_error(self) -> None:
        assert (
            is_stream_terminal({"type": "error", "message": "fail"}, "legacy") is True
        )

    def test_legacy_delta_not_terminal(self) -> None:
        assert is_stream_terminal({"type": "delta", "text": "hi"}, "legacy") is False
