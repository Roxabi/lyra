"""Tests for MessageDebouncer().merge() — pure merge semantics (issue #145)."""

from __future__ import annotations

from lyra.core.debouncer import MessageDebouncer
from lyra.core.message import Attachment
from tests.core.conftest import make_debouncer_msg

# ---------------------------------------------------------------------------
# MessageDebouncer().merge() — pure function tests
# ---------------------------------------------------------------------------


class TestMerge:
    """MessageDebouncer().merge() semantics."""

    def test_single_message_unchanged(self) -> None:
        """A single message is returned as-is (identity)."""
        msg = make_debouncer_msg("hello")
        result = MessageDebouncer().merge([msg])
        assert result is msg

    def test_two_messages_text_joined(self) -> None:
        """Two messages produce newline-joined text."""
        m1 = make_debouncer_msg("hello")
        m2 = make_debouncer_msg("world")
        result = MessageDebouncer().merge([m1, m2])
        assert result.text == "hello\nworld"
        assert result.text_raw == "hello\nworld"

    def test_three_messages_text_joined(self) -> None:
        """Three messages produce newline-joined text."""
        msgs = [
            make_debouncer_msg("a"),
            make_debouncer_msg("b"),
            make_debouncer_msg("c"),
        ]
        result = MessageDebouncer().merge(msgs)
        assert result.text == "a\nb\nc"

    def test_merge_uses_last_message_metadata(self) -> None:
        """Merged result uses id and timestamp from the last message."""
        m1 = make_debouncer_msg("first", msg_id="msg-1")
        m2 = make_debouncer_msg("second", msg_id="msg-2")
        result = MessageDebouncer().merge([m1, m2])
        assert result.id == "msg-2"

    def test_merge_is_mention_union(self) -> None:
        """is_mention is True if any constituent message was a mention."""
        m1 = make_debouncer_msg("hey", is_mention=False)
        m2 = make_debouncer_msg("@bot", is_mention=True)
        result = MessageDebouncer().merge([m1, m2])
        assert result.is_mention is True

    def test_merge_is_mention_all_false(self) -> None:
        """is_mention is False if no constituent message was a mention."""
        m1 = make_debouncer_msg("hey", is_mention=False)
        m2 = make_debouncer_msg("there", is_mention=False)
        result = MessageDebouncer().merge([m1, m2])
        assert result.is_mention is False

    def test_merge_concatenates_attachments(self) -> None:
        """Attachments from all messages are concatenated."""
        a1 = Attachment(
            type="image", url_or_path_or_bytes="url1", mime_type="image/png"
        )
        a2 = Attachment(
            type="file", url_or_path_or_bytes="url2", mime_type="text/plain"
        )
        m1 = make_debouncer_msg("with image", attachments=[a1])
        m2 = make_debouncer_msg("with file", attachments=[a2])
        result = MessageDebouncer().merge([m1, m2])
        assert len(result.attachments) == 2
        assert result.attachments[0] is a1
        assert result.attachments[1] is a2
