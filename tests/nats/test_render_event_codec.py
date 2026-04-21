"""Unit tests for NatsRenderEventCodec.decode() — schema version checks (issue #530).

These tests are purely in-process: they call NatsRenderEventCodec.decode() directly
with hand-crafted dicts and assert on the returned value + side effects.  No NATS
server is required.

MT-13 covers:
- text branch: match, legacy (no field), mismatch
- tool_summary branch: match, landmine mismatch (malformed files), counter isolation
"""

from __future__ import annotations

import logging

import pytest

from lyra.core.messaging.render_events import TextRenderEvent, ToolSummaryRenderEvent
from lyra.nats.render_event_codec import NatsRenderEventCodec


class TestRenderEventCodecVersionCheck:
    """NatsRenderEventCodec.decode() — schema version gate for text + tool_summary."""

    # -----------------------------------------------------------------------
    # text branch — acceptance cases
    # -----------------------------------------------------------------------

    def test_text_match_decodes(self) -> None:
        """v1 text payload decodes to TextRenderEvent; counter stays empty."""
        # Arrange
        codec = NatsRenderEventCodec()
        counter: dict[str, int] = {}

        # Act
        result = codec.decode(
            "text",
            {"schema_version": 1, "text": "hi", "is_final": True},
            counter=counter,
        )

        # Assert
        assert isinstance(result, TextRenderEvent)
        assert result.text == "hi"
        assert result.is_final is True
        assert counter == {}

    def test_text_legacy_decodes(self) -> None:
        """text payload without schema_version defaults to v1 and decodes normally."""
        # Arrange
        codec = NatsRenderEventCodec()
        counter: dict[str, int] = {}

        # Act
        result = codec.decode(
            "text",
            {"text": "hi", "is_final": True},
            counter=counter,
        )

        # Assert — missing field → legacy v1 path → accepted
        assert isinstance(result, TextRenderEvent)
        assert result.text == "hi"
        assert result.is_final is True
        assert counter == {}

    # -----------------------------------------------------------------------
    # text branch — mismatch
    # -----------------------------------------------------------------------

    def test_text_mismatch_drops(self, caplog: pytest.LogCaptureFixture) -> None:
        """v2 text payload returns None, increments counter, emits log.error."""
        # Arrange
        codec = NatsRenderEventCodec()
        counter: dict[str, int] = {}

        # Act
        with caplog.at_level(logging.ERROR, logger="lyra.nats._version_check"):
            result = codec.decode(
                "text",
                {"schema_version": 2, "text": "hi", "is_final": True},
                counter=counter,
            )

        # Assert — dropped
        assert result is None
        # Assert — counter incremented for this envelope
        assert counter == {"TextRenderEvent:schema": 1}
        # Assert — exactly one ERROR log with expected substring
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(error_records) == 1
        assert "NATS schema version mismatch" in error_records[0].getMessage()

    # -----------------------------------------------------------------------
    # tool_summary branch — acceptance
    # -----------------------------------------------------------------------

    def test_tool_summary_match_decodes(self) -> None:
        """v1 tool_summary payload decodes to ToolSummaryRenderEvent; counter empty."""
        # Arrange
        codec = NatsRenderEventCodec()
        counter: dict[str, int] = {}

        # Act
        result = codec.decode(
            "tool_summary",
            {
                "schema_version": 1,
                "files": {},
                "bash_commands": [],
                "web_fetches": [],
                "agent_calls": [],
                "silent_counts": {},
                "is_complete": False,
            },
            counter=counter,
        )

        # Assert
        assert isinstance(result, ToolSummaryRenderEvent)
        assert counter == {}

    # -----------------------------------------------------------------------
    # tool_summary branch — landmine mismatch
    # -----------------------------------------------------------------------

    def test_tool_summary_mismatch_short_circuits_manual_extraction(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Version check fires BEFORE manual payload extraction in tool_summary branch.

        The 'files' value is a string, not a dict.  If execution ever reached
        the `payload.get("files", {}).items()` line it would raise AttributeError.
        A passing test proves the version gate short-circuits before the landmine.
        """
        # Arrange — payload that would blow up if extracted
        codec = NatsRenderEventCodec()
        counter: dict[str, int] = {}
        landmine_payload = {
            "schema_version": 2,
            "files": "not-a-dict",  # AttributeError on .items() if reached
        }

        # Act — must NOT raise
        with caplog.at_level(logging.ERROR, logger="lyra.nats._version_check"):
            result = codec.decode(
                "tool_summary",
                landmine_payload,
                counter=counter,
            )

        # Assert — dropped without raising
        assert result is None
        assert counter == {"ToolSummaryRenderEvent:schema": 1}
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(error_records) == 1
        assert "NATS schema version mismatch" in error_records[0].getMessage()

    # -----------------------------------------------------------------------
    # Counter isolation
    # -----------------------------------------------------------------------

    def test_counter_isolation_between_decodes(self) -> None:
        """Two independent counter dicts accumulate only their own drops."""
        # Arrange
        codec = NatsRenderEventCodec()
        c1: dict[str, int] = {}
        c2: dict[str, int] = {}

        # Act — two drops into c1
        codec.decode(
            "text",
            {"schema_version": 2, "text": "x", "is_final": True},
            counter=c1,
        )
        codec.decode(
            "text",
            {"schema_version": 2, "text": "x", "is_final": True},
            counter=c1,
        )
        # One drop into c2
        codec.decode(
            "text",
            {"schema_version": 2, "text": "y", "is_final": True},
            counter=c2,
        )

        # Assert — counts are independent
        assert c1 == {"TextRenderEvent:schema": 2}
        assert c2 == {"TextRenderEvent:schema": 1}
