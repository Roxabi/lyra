"""StreamState soft-error display (ADR-089)."""

from __future__ import annotations

from factory.outbound._streaming_state import StreamState


class TestStreamStateRunErrorMessage:
    def test_build_display_text_uses_run_error_when_no_final_text(self) -> None:
        state = StreamState()
        state.is_error_pending = True
        state.run_error_message = "You've hit your weekly limit"

        display = state.build_display_text(lambda _key, fallback: fallback)

        assert display == "❌ You've hit your weekly limit"

    def test_build_display_text_run_error_without_pending_omits_prefix(self) -> None:
        state = StreamState()
        state.is_error_pending = False
        state.run_error_message = "You've hit your weekly limit"

        display = state.build_display_text(lambda _key, fallback: fallback)

        assert display == "You've hit your weekly limit"