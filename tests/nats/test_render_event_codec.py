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
import typing

import pytest

from lyra.core.messaging.render_events import (
    ReasoningDeltaRenderEvent,
    ReasoningEndRenderEvent,
    ReasoningStartRenderEvent,
    RenderEvent,
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

        event_type, payload, _ = codec.encode(original)
        decoded = codec.decode(event_type, payload)
        assert decoded == original

    def test_tool_call_is_terminal_false(self) -> None:
        # ToolCall* are mid-stream — never terminal sentinels.
        codec = NatsRenderEventCodec()
        for et in (
            "tool_call_start",
            "tool_call_args",
            "tool_call_end",
            "tool_call_result",
        ):
            assert codec.is_terminal(et) is False

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
    """Runtime tripwire for the registry completeness guard in encode().

    Pyright catches union-exhaustiveness at static-check time, but pyright
    config drift, stub regeneration, or accidental union widening can all
    silently disable the static check while leaving a live crash path.
    The original Slice 2 (#1099) incident was exactly this class of bug:
    a new RenderEvent subclass reached encode() at runtime with no branch
    to handle it. With the registry design, encode() raises TypeError when
    the event type is not registered — the test asserts that exact exception.
    """

    def test_encode_raises_for_unknown_render_event(self) -> None:
        # Fake RenderEvent-shaped dataclass NOT in the union — registry
        # lookup returns None, encode() raises TypeError.
        from dataclasses import dataclass

        @dataclass(frozen=True)
        class _FakeRenderEvent:
            payload: str = "x"
            schema_version: int = 1

        codec = NatsRenderEventCodec()
        with pytest.raises(TypeError, match="unregistered RenderEvent type"):
            codec.encode(_FakeRenderEvent())  # pyright: ignore[reportArgumentType]


# ---------------------------------------------------------------------------
# T-1: Registry completeness (Slice 2 — #1192)
# ---------------------------------------------------------------------------


class TestRegistryCompleteness:
    """Registry must contain exactly the RenderEvent union members — no more, no less.

    TestRegistryCompleteness.test_registry_covers_full_union will fail loud at CI
    whenever a new union member is added to RenderEvent without a matching
    registry entry.  This is the machine-enforced exhaustiveness proof that
    replaces the assert_never / isinstance chain.
    """

    def test_registry_covers_full_union(self) -> None:
        codec = NatsRenderEventCodec()
        union_members = set(typing.get_args(RenderEvent))
        registry_keys = set(codec._registry.keys())
        assert registry_keys == union_members, (
            f"Registry mismatch.\n"
            f"  In union but not registry: {union_members - registry_keys}\n"
            f"  In registry but not union: {registry_keys - union_members}"
        )

    def test_registry_branches_well_formed(self) -> None:
        codec = NatsRenderEventCodec()
        branches = list(codec._registry.values())

        # Each event_type string must be non-empty
        for branch in branches:
            assert branch.event_type, f"{branch.cls_name} has empty event_type"

        # event_type strings must be unique across _by_type_str
        type_strings = [b.event_type for b in branches]
        assert len(type_strings) == len(set(type_strings)), (
            f"Duplicate event_type strings: "
            f"{[t for t in type_strings if type_strings.count(t) > 1]}"
        )

        # cls_name must match the registry key class __name__
        for cls, branch in codec._registry.items():
            assert branch.cls_name == cls.__name__, (
                f"Branch cls_name {branch.cls_name!r} != class"
                f" __name__ {cls.__name__!r}"
            )


# ---------------------------------------------------------------------------
# T-2: Per-branch decode error isolation (Slice 2 — #1192)
# ---------------------------------------------------------------------------


class TestDecodeErrorAbort:
    """Per-branch: malformed payload → decode returns None + log.exception."""

    def _assert_decode_returns_none_with_exception_log(
        self,
        codec: NatsRenderEventCodec,
        event_type: str,
        bad_payload: dict,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.ERROR, logger="lyra.nats.render_event_codec"):
            result = codec.decode(event_type, bad_payload)
        assert result is None, f"Expected None for {event_type!r} with bad payload"
        # Substring matches `log.exception(...)` at render_event_codec.py decode() —
        # update both if the codec's message string changes.
        exception_records = [
            r
            for r in caplog.records
            if r.levelno >= logging.ERROR
            and r.name == "lyra.nats.render_event_codec"
            and "decode failed" in r.getMessage()
        ]
        assert exception_records, (
            f"Expected exception-level log for {event_type!r} bad payload decode"
        )

    @pytest.mark.parametrize(
        "event_type,bad_payload",
        [
            # text — missing required field 'text'
            ("text", {"schema_version": 1, "is_final": True}),
            # text_start — missing required field 'message_id'
            ("text_start", {"schema_version": 1}),
            # text_delta — missing required field 'delta'
            ("text_delta", {"schema_version": 1, "message_id": "m1"}),
            # text_end — missing required field 'message_id'
            ("text_end", {"schema_version": 1}),
            # text_chunk — missing required field 'delta'
            ("text_chunk", {"schema_version": 1, "message_id": "m1"}),
            # tool_summary — files is a string → AttributeError on .items()
            ("tool_summary", {"schema_version": 1, "files": "not-a-dict"}),
            # run_started — missing required field 'run_id'
            ("run_started", {"schema_version": 1}),
            # run_finished — missing required field 'run_id'
            ("run_finished", {"schema_version": 1}),
            # run_error — missing required fields 'run_id' and 'message'
            ("run_error", {"schema_version": 1}),
            # tool_call_start — missing required fields 'tool_call_id' + 'tool_name'
            ("tool_call_start", {"schema_version": 1}),
            # tool_call_args — missing required field 'delta'
            ("tool_call_args", {"schema_version": 1, "tool_call_id": "tc1"}),
            # tool_call_end — missing required field 'tool_call_id'
            ("tool_call_end", {"schema_version": 1}),
            # tool_call_result — missing required field 'content'
            ("tool_call_result", {"schema_version": 1, "tool_call_id": "tc1"}),
            # reasoning_start — missing required field 'message_id'
            ("reasoning_start", {"schema_version": 1}),
            # reasoning_delta — missing required field 'delta'
            ("reasoning_delta", {"schema_version": 1, "message_id": "m1"}),
            # reasoning_end — missing required field 'message_id'
            ("reasoning_end", {"schema_version": 1}),
        ],
    )
    def test_decode_aborts_on_bad_payload(
        self,
        event_type: str,
        bad_payload: dict,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        codec = NatsRenderEventCodec()
        self._assert_decode_returns_none_with_exception_log(
            codec, event_type, bad_payload, caplog
        )


# ---------------------------------------------------------------------------
# T-3: Decoder None-first ordering (Slice 2 — #1192)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decode_missing_event_type_breaks_first(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """decode_stream_events breaks on None event_type BEFORE the stream_error check.

    Injects a chunk with no ``event_type`` field into the decoder queue and
    asserts that:
    1. The decoder emits a warning log about the missing field.
    2. The stream terminates (the async generator completes without yielding
       any event).
    3. No subsequent chunk is processed — the break happens before stream_error
       would be evaluated.

    This directly verifies the strict 3-branch ordering in
    ``decode_stream_events``: ``event_type is None`` → ``log.warning + break``
    fires before the ``event_type == "stream_error"`` branch.
    """
    import asyncio

    from lyra.adapters.nats.nats_stream_decoder import decode_stream_events

    q: asyncio.Queue[dict] = asyncio.Queue()
    # Chunk with no event_type — the None-check branch must fire first.
    await q.put({})

    events = []
    with caplog.at_level(
        logging.WARNING, logger="lyra.adapters.nats.nats_stream_decoder"
    ):
        async for event in decode_stream_events("test-stream", q):
            events.append(event)

    # No events yielded — stream aborted on None event_type
    assert events == []

    # Warning about missing event_type must have been emitted
    missing_warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "missing event_type" in r.getMessage()
    ]
    assert missing_warnings, "Expected warning about missing event_type field"


# ---------------------------------------------------------------------------
# T-8: is_terminal covers synthetic sentinels (Slice 2 — #1192)
# ---------------------------------------------------------------------------


def test_is_terminal_synthetic_sentinels() -> None:
    """is_terminal returns True for both synthetic terminal sentinels."""
    codec = NatsRenderEventCodec()
    assert codec.is_terminal("stream_end") is True
    assert codec.is_terminal("stream_error") is True


# ---------------------------------------------------------------------------
# T-9: decode synthetic sentinels — no warning log (Slice 2 — #1192)
# ---------------------------------------------------------------------------


def test_decode_synthetic_sentinel_no_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """decode("stream_end", {}) returns None without log.warning("unknown event_type").

    Asserts that _SYNTHETIC_TERMINALS short-circuits the registry lookup so the
    codec never reaches the "unknown event_type" warning path for these sentinels.
    """
    codec = NatsRenderEventCodec()
    with caplog.at_level(logging.WARNING, logger="lyra.nats.render_event_codec"):
        result_end = codec.decode("stream_end", {})
        result_error = codec.decode("stream_error", {})

    assert result_end is None
    assert result_error is None

    # No warning about "unknown event_type" must have been emitted
    unknown_warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "unknown event_type" in r.getMessage()
    ]
    assert not unknown_warnings, (
        f"Unexpected 'unknown event_type' warning(s) for synthetic sentinels: "
        f"{[r.getMessage() for r in unknown_warnings]}"
    )
