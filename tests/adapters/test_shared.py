from unittest.mock import AsyncMock, patch

import pytest

from lyra.adapters._shared import (
    IntermediateTextState,
    chunk_text,
    format_tool_summary_header,
    send_with_retry,
)
from lyra.core.messaging.render_events import ToolSummaryRenderEvent


class TestChunkText:
    def test_empty_string_returns_empty_list(self) -> None:
        assert chunk_text("", 10) == []

    def test_short_text_single_chunk(self) -> None:
        assert chunk_text("hello", 10) == ["hello"]

    def test_text_split_at_exact_boundary(self) -> None:
        assert chunk_text("abcdef", 3) == ["abc", "def"]

    def test_text_longer_than_max_len(self) -> None:
        result = chunk_text("a" * 10, 3)
        assert result == ["aaa", "aaa", "aaa", "a"]

    def test_max_len_of_one(self) -> None:
        assert chunk_text("abc", 1) == ["a", "b", "c"]

    def test_invalid_max_len_raises(self) -> None:
        with pytest.raises(ValueError, match="max_len"):
            chunk_text("x", 0)
        with pytest.raises(ValueError, match="max_len"):
            chunk_text("x", -1)

    def test_escape_fn_applied_before_chunking(self) -> None:
        # escape_fn doubles every character — max_len applies to post-escape length
        result = chunk_text("abc", 4, escape_fn=lambda t: "".join(c * 2 for c in t))
        # "aabbcc" split at 4 → ["aabb", "cc"]
        assert result == ["aabb", "cc"]

    def test_escape_fn_on_empty_returns_empty(self) -> None:
        assert chunk_text("", 10, escape_fn=str.upper) == []

    def test_no_escape_fn(self) -> None:
        # chunk_text splits at word boundaries, so "hello world" splits at the
        # space — each chunk is rstrip'd/lstrip'd, yielding two clean words.
        assert chunk_text("hello world", 5) == ["hello", "world"]


class TestIntermediateTextState:
    def test_fresh_instance_has_no_text(self) -> None:
        state = IntermediateTextState()
        assert state.text == ""
        assert not state.has_intermediate_text

    def test_append_first_segment_adds_hourglass_prefix(self) -> None:
        state = IntermediateTextState()
        state.append("hello")
        assert state.text == "⏳ hello"
        assert state.has_intermediate_text

    def test_append_second_segment_adds_newline_and_prefix(self) -> None:
        state = IntermediateTextState()
        state.append("hello")
        state.append("world")
        assert state.text == "⏳ hello\n⏳ world"

    def test_append_empty_string_is_noop(self) -> None:
        state = IntermediateTextState()
        state.append("")
        assert state.text == ""
        assert not state.has_intermediate_text

    def test_append_multiple_segments_chains_correctly(self) -> None:
        state = IntermediateTextState()
        state.append("a")
        state.append("b")
        state.append("c")
        assert state.text == "⏳ a\n⏳ b\n⏳ c"

    def test_display_returns_empty_string_when_both_empty(self) -> None:
        state = IntermediateTextState()
        assert state.display() == ""

    def test_display_returns_text_only_when_no_tool_summary(self) -> None:
        state = IntermediateTextState()
        state.append("thinking")
        assert state.display() == "⏳ thinking"

    def test_display_returns_tool_summary_only_when_no_text(self) -> None:
        state = IntermediateTextState()
        state.set_tool_summary("🔧 Done ✅")
        assert state.display() == "🔧 Done ✅"

    def test_display_combines_text_and_summary_with_double_newline(self) -> None:
        state = IntermediateTextState()
        state.append("thinking")
        state.set_tool_summary("🔧 Done ✅")
        assert state.display() == "⏳ thinking\n\n🔧 Done ✅"

    def test_display_combine_recap_false_returns_text_when_both_set(self) -> None:
        state = IntermediateTextState()
        state.append("thinking")
        state.set_tool_summary("🔧 Done ✅")
        assert state.display(combine_recap=False) == "⏳ thinking"

    def test_display_combine_recap_false_returns_summary_when_no_text(self) -> None:
        state = IntermediateTextState()
        state.set_tool_summary("🔧 Done ✅")
        assert state.display(combine_recap=False) == "🔧 Done ✅"

    def test_set_tool_summary_overwrites_previous(self) -> None:
        state = IntermediateTextState()
        state.set_tool_summary("first")
        state.set_tool_summary("second")
        assert state.display() == "second"


class TestSendWithRetry:
    async def test_send_with_retry_succeeds_on_first_attempt(self) -> None:
        # Arrange
        coro_fn = AsyncMock(return_value=None)
        # Act
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await send_with_retry(coro_fn, label="test-op")
        # Assert
        coro_fn.assert_awaited_once()

    async def test_send_with_retry_retries_on_transient_failure(self) -> None:
        # Arrange — fails once, then succeeds
        coro_fn = AsyncMock(side_effect=[RuntimeError("transient"), None])
        # Act
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await send_with_retry(coro_fn, label="test-op")
        # Assert
        assert coro_fn.await_count == 2

    async def test_send_with_retry_gives_up_after_max_attempts(self) -> None:
        # Arrange — always raises
        coro_fn = AsyncMock(side_effect=RuntimeError("permanent"))
        # Act — must NOT re-raise; function returns normally after exhausting attempts
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await send_with_retry(coro_fn, label="test-op", max_attempts=3)
        # Assert
        assert coro_fn.await_count == 3

    async def test_send_with_retry_custom_max_attempts(self) -> None:
        # Arrange — always raises; custom limit of 2
        coro_fn = AsyncMock(side_effect=RuntimeError("permanent"))
        # Act
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await send_with_retry(coro_fn, label="test-op", max_attempts=2)
        # Assert — called at most max_attempts times
        assert coro_fn.await_count == 2


class TestFormatToolSummaryHeader:
    def test_format_tool_summary_header_complete(self) -> None:
        # Arrange
        event = ToolSummaryRenderEvent(is_complete=True)
        # Act
        result = format_tool_summary_header(event)
        # Assert
        assert result == "🔧 Done ✅"

    def test_format_tool_summary_header_in_progress(self) -> None:
        # Arrange
        event = ToolSummaryRenderEvent(is_complete=False)
        # Act
        result = format_tool_summary_header(event)
        # Assert
        assert result == "🔧 Working…"
