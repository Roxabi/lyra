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

from lyra.core.messaging.render_events import (
    ReasoningDeltaRenderEvent,
    ReasoningEndRenderEvent,
    ReasoningStartRenderEvent,
    TextChunkRenderEvent,
    TextDeltaRenderEvent,
    TextEndRenderEvent,
    TextRenderEvent,
    TextStartRenderEvent,
    ToolCallArgsRenderEvent,
    ToolCallEndRenderEvent,
    ToolCallResultRenderEvent,
    ToolCallStartRenderEvent,
    ToolSummaryRenderEvent,
)
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


# ---------------------------------------------------------------------------
# ToolCall* round-trip + schema floor (Slice 3 of #1096)
# ---------------------------------------------------------------------------


class TestToolCallCodecRoundTrip:
    """Encode then decode each ToolCall* event and assert byte-for-byte fidelity."""

    def test_tool_call_start_round_trip(self) -> None:
        codec = NatsRenderEventCodec()
        original = ToolCallStartRenderEvent(tool_call_id="toolu_AB", tool_name="Read")

        event_type, payload, is_done = codec.encode(original)

        assert event_type == "tool_call_start"
        assert is_done is False
        assert payload["tool_call_id"] == "toolu_AB"
        assert payload["tool_name"] == "Read"
        assert payload["schema_version"] == 1

        decoded = codec.decode(event_type, payload)
        assert decoded == original

    def test_tool_call_args_round_trip(self) -> None:
        codec = NatsRenderEventCodec()
        original = ToolCallArgsRenderEvent(tool_call_id="toolu_AB", delta='{"foo":')

        event_type, payload, is_done = codec.encode(original)

        assert event_type == "tool_call_args"
        assert is_done is False
        decoded = codec.decode(event_type, payload)
        assert decoded == original

    def test_tool_call_end_round_trip(self) -> None:
        codec = NatsRenderEventCodec()
        original = ToolCallEndRenderEvent(tool_call_id="toolu_AB")

        event_type, payload, is_done = codec.encode(original)

        assert event_type == "tool_call_end"
        assert is_done is False
        decoded = codec.decode(event_type, payload)
        assert decoded == original

    def test_tool_call_result_round_trip(self) -> None:
        codec = NatsRenderEventCodec()
        original = ToolCallResultRenderEvent(
            tool_call_id="toolu_AB", content="ok", is_error=False
        )

        event_type, payload, is_done = codec.encode(original)

        assert event_type == "tool_call_result"
        assert is_done is False
        decoded = codec.decode(event_type, payload)
        assert decoded == original

    def test_tool_call_result_is_error_round_trip(self) -> None:
        codec = NatsRenderEventCodec()
        original = ToolCallResultRenderEvent(
            tool_call_id="toolu_AB", content="boom", is_error=True
        )

        event_type, payload, _is_done = codec.encode(original)
        decoded = codec.decode(event_type, payload)
        assert decoded == original

    def test_tool_call_is_terminal_false(self) -> None:
        # ToolCall* are mid-stream — never terminal sentinels.
        for et in (
            "tool_call_start",
            "tool_call_args",
            "tool_call_end",
            "tool_call_result",
        ):
            assert NatsRenderEventCodec.is_terminal(et) is False

    def test_tool_call_start_schema_floor_drops(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        codec = NatsRenderEventCodec()
        counter: dict[str, int] = {}

        with caplog.at_level(logging.ERROR, logger="lyra.nats._version_check"):
            result = codec.decode(
                "tool_call_start",
                {"schema_version": 99, "tool_call_id": "x", "tool_name": "Read"},
                counter=counter,
            )

        assert result is None
        assert counter == {"ToolCallStartRenderEvent:schema": 1}

    def test_tool_call_args_schema_floor_drops(self) -> None:
        codec = NatsRenderEventCodec()
        counter: dict[str, int] = {}
        result = codec.decode(
            "tool_call_args",
            {"schema_version": 99, "tool_call_id": "x", "delta": "y"},
            counter=counter,
        )
        assert result is None
        assert counter == {"ToolCallArgsRenderEvent:schema": 1}


# ---------------------------------------------------------------------------
# Reasoning* round-trip + schema floor (Slice 4 of #1096)
# ---------------------------------------------------------------------------


class TestReasoningCodecRoundTrip:
    """Encode then decode each Reasoning* event and assert field fidelity."""

    def test_reasoning_start_roundtrip(self) -> None:
        codec = NatsRenderEventCodec()
        original = ReasoningStartRenderEvent(message_id="msg_001")

        event_type, payload, is_done = codec.encode(original)

        assert event_type == "reasoning_start"
        assert is_done is False
        assert payload["message_id"] == "msg_001"
        assert payload["schema_version"] == 1

        decoded = codec.decode(event_type, payload)
        assert decoded == original

    def test_reasoning_delta_roundtrip(self) -> None:
        codec = NatsRenderEventCodec()
        original = ReasoningDeltaRenderEvent(message_id="msg_001", delta="thinking...")

        event_type, payload, is_done = codec.encode(original)

        assert event_type == "reasoning_delta"
        assert is_done is False
        assert payload["message_id"] == "msg_001"
        assert payload["delta"] == "thinking..."
        assert payload["schema_version"] == 1

        decoded = codec.decode(event_type, payload)
        assert decoded == original

    def test_reasoning_end_roundtrip(self) -> None:
        codec = NatsRenderEventCodec()
        original = ReasoningEndRenderEvent(message_id="msg_001")

        event_type, payload, is_done = codec.encode(original)

        assert event_type == "reasoning_end"
        assert is_done is False
        assert payload["message_id"] == "msg_001"
        assert payload["schema_version"] == 1

        decoded = codec.decode(event_type, payload)
        assert decoded == original

    def test_reasoning_floor_rejects_future_schema(self) -> None:
        codec = NatsRenderEventCodec()
        counter: dict[str, int] = {}

        result = codec.decode(
            "reasoning_start",
            {"schema_version": 99, "message_id": "msg_001"},
            counter=counter,
        )

        assert result is None
        assert counter == {"ReasoningStartRenderEvent:schema": 1}


class TestRenderEventCodecTextTriplet:
    """Slice 2 (#1099) v2 Text triplet — encode + round-trip coverage.

    Guards against the Slice 2 codec gap where TextStart/Delta/End/Chunk
    were emitted by StreamProcessor but unhandled by NatsRenderEventCodec,
    crashing the hub→adapter stream with TypeError.
    """

    def test_text_start_encodes(self) -> None:
        codec = NatsRenderEventCodec()
        event_type, payload, is_done = codec.encode(
            TextStartRenderEvent(message_id="msg_001")
        )
        assert event_type == "text_start"
        assert is_done is False
        assert payload["message_id"] == "msg_001"
        assert payload["schema_version"] == 1

    def test_text_delta_encodes(self) -> None:
        codec = NatsRenderEventCodec()
        event_type, payload, is_done = codec.encode(
            TextDeltaRenderEvent(message_id="msg_001", delta="hello")
        )
        assert event_type == "text_delta"
        assert is_done is False
        assert payload["message_id"] == "msg_001"
        assert payload["delta"] == "hello"
        assert payload["schema_version"] == 1

    def test_text_end_encodes(self) -> None:
        codec = NatsRenderEventCodec()
        event_type, payload, is_done = codec.encode(
            TextEndRenderEvent(message_id="msg_001")
        )
        assert event_type == "text_end"
        assert is_done is False
        assert payload["message_id"] == "msg_001"
        assert payload["schema_version"] == 1

    def test_text_chunk_encodes(self) -> None:
        codec = NatsRenderEventCodec()
        event_type, payload, is_done = codec.encode(
            TextChunkRenderEvent(message_id="msg_001", delta="hi")
        )
        assert event_type == "text_chunk"
        assert is_done is False
        assert payload["message_id"] == "msg_001"
        assert payload["delta"] == "hi"
        assert payload["schema_version"] == 1

    def test_text_start_round_trip(self) -> None:
        codec = NatsRenderEventCodec()
        original = TextStartRenderEvent(message_id="msg_001")
        event_type, payload, _ = codec.encode(original)
        decoded = codec.decode(event_type, payload)
        assert decoded == original

    def test_text_delta_round_trip(self) -> None:
        codec = NatsRenderEventCodec()
        original = TextDeltaRenderEvent(message_id="msg_001", delta="hello world")
        event_type, payload, _ = codec.encode(original)
        decoded = codec.decode(event_type, payload)
        assert decoded == original

    def test_text_end_round_trip(self) -> None:
        codec = NatsRenderEventCodec()
        original = TextEndRenderEvent(message_id="msg_001")
        event_type, payload, _ = codec.encode(original)
        decoded = codec.decode(event_type, payload)
        assert decoded == original

    def test_text_chunk_round_trip(self) -> None:
        codec = NatsRenderEventCodec()
        original = TextChunkRenderEvent(message_id="msg_001", delta="hi")
        event_type, payload, _ = codec.encode(original)
        decoded = codec.decode(event_type, payload)
        assert decoded == original

    def test_text_start_floor_rejects_future_schema(self) -> None:
        codec = NatsRenderEventCodec()
        counter: dict[str, int] = {}
        result = codec.decode(
            "text_start",
            {"schema_version": 99, "message_id": "msg_001"},
            counter=counter,
        )
        assert result is None
        assert counter == {"TextStartRenderEvent:schema": 1}

    def test_text_delta_floor_rejects_future_schema(self) -> None:
        codec = NatsRenderEventCodec()
        counter: dict[str, int] = {}
        result = codec.decode(
            "text_delta",
            {"schema_version": 99, "message_id": "msg_001", "delta": "x"},
            counter=counter,
        )
        assert result is None
        assert counter == {"TextDeltaRenderEvent:schema": 1}

    def test_text_end_floor_rejects_future_schema(self) -> None:
        codec = NatsRenderEventCodec()
        counter: dict[str, int] = {}
        result = codec.decode(
            "text_end",
            {"schema_version": 99, "message_id": "msg_001"},
            counter=counter,
        )
        assert result is None
        assert counter == {"TextEndRenderEvent:schema": 1}

    def test_text_chunk_floor_rejects_future_schema(self) -> None:
        codec = NatsRenderEventCodec()
        counter: dict[str, int] = {}
        result = codec.decode(
            "text_chunk",
            {"schema_version": 99, "message_id": "msg_001", "delta": "x"},
            counter=counter,
        )
        assert result is None
        assert counter == {"TextChunkRenderEvent:schema": 1}


class TestRenderEventCodecExhaustivenessGuard:
    """Runtime tripwire for the assert_never guard in encode().

    Pyright catches union-exhaustiveness at static-check time, but pyright
    config drift, stub regeneration, or accidental union widening can all
    silently disable the static check while leaving a live crash path.
    The original Slice 2 (#1099) incident was exactly this class of bug:
    a new RenderEvent subclass reached encode() at runtime with no branch
    to handle it. This test fires if assert_never is ever removed or
    bypassed — uses a serializable dataclass that survives the upstream
    serialize() call and reaches the isinstance dispatch chain.
    """

    def test_encode_assert_never_fires_on_unknown_render_event(self) -> None:
        # Fake RenderEvent-shaped dataclass NOT in the union. serialize()
        # accepts it (it's a dataclass); the isinstance ladder falls through
        # every branch; assert_never raises AssertionError.
        from dataclasses import dataclass

        @dataclass(frozen=True)
        class _FakeRenderEvent:
            payload: str = "x"
            schema_version: int = 1

        codec = NatsRenderEventCodec()
        with pytest.raises(AssertionError):
            codec.encode(_FakeRenderEvent())  # pyright: ignore[reportArgumentType]
