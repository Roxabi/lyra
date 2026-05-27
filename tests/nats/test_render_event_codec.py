"""Unit tests for NatsRenderEventCodec.decode() — schema version checks.

These tests are purely in-process: they call NatsRenderEventCodec.decode() directly
with hand-crafted dicts and assert on the returned value + side effects.  No NATS
server is required.

v1 TextRenderEvent and ToolSummaryRenderEvent branches were removed in #1192 S3.
"""

from __future__ import annotations

import logging
import typing

import pytest

from lyra.core.messaging.render_events import (
    SCHEMA_VERSION_REASONING_DELTA_RENDER_EVENT,
    SCHEMA_VERSION_REASONING_END_RENDER_EVENT,
    SCHEMA_VERSION_REASONING_START_RENDER_EVENT,
    SCHEMA_VERSION_RUN_ERROR_RENDER_EVENT,
    SCHEMA_VERSION_RUN_FINISHED_RENDER_EVENT,
    SCHEMA_VERSION_RUN_STARTED_RENDER_EVENT,
    SCHEMA_VERSION_TEXT_CHUNK_RENDER_EVENT,
    SCHEMA_VERSION_TEXT_DELTA_RENDER_EVENT,
    SCHEMA_VERSION_TEXT_END_RENDER_EVENT,
    SCHEMA_VERSION_TEXT_START_RENDER_EVENT,
    SCHEMA_VERSION_TOOL_CALL_ARGS_RENDER_EVENT,
    SCHEMA_VERSION_TOOL_CALL_END_RENDER_EVENT,
    SCHEMA_VERSION_TOOL_CALL_RESULT_RENDER_EVENT,
    SCHEMA_VERSION_TOOL_CALL_START_RENDER_EVENT,
    ReasoningDeltaRenderEvent,
    ReasoningEndRenderEvent,
    ReasoningStartRenderEvent,
    RenderEvent,
    TextChunkRenderEvent,
    TextDeltaRenderEvent,
    TextEndRenderEvent,
    TextStartRenderEvent,
    ToolCallArgsRenderEvent,
    ToolCallEndRenderEvent,
    ToolCallResultRenderEvent,
    ToolCallStartRenderEvent,
)
from lyra.nats.render_event_codec import NatsRenderEventCodec


class TestRenderEventCodecVersionCheck:
    """NatsRenderEventCodec.decode() — schema version gate for text_start branch."""

    def test_text_start_match_decodes(self) -> None:
        """v1 text_start payload decodes; counter stays empty."""
        codec = NatsRenderEventCodec()
        counter: dict[str, int] = {}

        result = codec.decode(
            "text_start",
            {"schema_version": 1, "message_id": "msg_001"},
            counter=counter,
        )

        assert isinstance(result, TextStartRenderEvent)
        assert result.message_id == "msg_001"
        assert counter == {}

    def test_text_start_legacy_decodes(self) -> None:
        """text_start payload without schema_version defaults to v1."""
        codec = NatsRenderEventCodec()
        counter: dict[str, int] = {}

        result = codec.decode(
            "text_start",
            {"message_id": "msg_002"},
            counter=counter,
        )

        assert isinstance(result, TextStartRenderEvent)
        assert counter == {}

    def test_text_start_mismatch_drops(self, caplog: pytest.LogCaptureFixture) -> None:
        """Future schema payload returns None, increments counter."""
        codec = NatsRenderEventCodec()
        counter: dict[str, int] = {}

        with caplog.at_level(logging.ERROR, logger="lyra.nats._version_check"):
            result = codec.decode(
                "text_start",
                {"schema_version": 99, "message_id": "msg_001"},
                counter=counter,
            )

        assert result is None
        assert counter == {"TextStartRenderEvent:schema": 1}
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(error_records) == 1
        assert "NATS schema version mismatch" in error_records[0].getMessage()

    def test_unknown_event_type_drops(self, caplog: pytest.LogCaptureFixture) -> None:
        """Unknown event_type returns None and emits warning."""
        codec = NatsRenderEventCodec()
        counter: dict[str, int] = {}

        with caplog.at_level(logging.WARNING, logger="lyra.nats.render_event_codec"):
            result = codec.decode(
                "text",
                {"schema_version": 1, "text": "hi", "is_final": True},
                counter=counter,
            )

        assert result is None
        assert "unknown:text" in counter

    def test_counter_isolation_between_decodes(self) -> None:
        """Two independent counter dicts accumulate only their own drops."""
        codec = NatsRenderEventCodec()
        c1: dict[str, int] = {}
        c2: dict[str, int] = {}

        codec.decode(
            "text_start", {"schema_version": 99, "message_id": "m"}, counter=c1
        )
        codec.decode(
            "text_start", {"schema_version": 99, "message_id": "m"}, counter=c1
        )
        codec.decode(
            "text_start", {"schema_version": 99, "message_id": "m"}, counter=c2
        )

        assert c1 == {"TextStartRenderEvent:schema": 2}
        assert c2 == {"TextStartRenderEvent:schema": 1}


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

    def test_tool_call_start_with_input_round_trip(self) -> None:
        """Clipool path: input dict is encoded, decoded, and preserved."""
        codec = NatsRenderEventCodec()
        original = ToolCallStartRenderEvent(
            tool_call_id="toolu_AB",
            tool_name="Bash",
            input={"command": "git log"},
        )

        event_type, payload, is_done = codec.encode(original)

        assert event_type == "tool_call_start"
        assert is_done is False
        assert payload["input"] == {"command": "git log"}

        decoded = codec.decode(event_type, payload)
        assert decoded == original
        assert decoded.input == {"command": "git log"}  # type: ignore[union-attr]

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
    """Slice 2 (#1099) v2 Text triplet — encode + round-trip coverage."""

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
    """Runtime tripwire for the registry completeness guard in encode()."""

    def test_encode_raises_for_unknown_render_event(self) -> None:
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
    """Registry must contain exactly the RenderEvent union members."""

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

        for branch in branches:
            assert branch.event_type, f"{branch.cls_name} has empty event_type"

        type_strings = [b.event_type for b in branches]
        assert len(type_strings) == len(set(type_strings)), (
            f"Duplicate event_type strings: "
            f"{[t for t in type_strings if type_strings.count(t) > 1]}"
        )

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
            # text_start — missing required field 'message_id'
            ("text_start", {"schema_version": SCHEMA_VERSION_TEXT_START_RENDER_EVENT}),
            # text_delta — missing required field 'delta'
            (
                "text_delta",
                {
                    "schema_version": SCHEMA_VERSION_TEXT_DELTA_RENDER_EVENT,
                    "message_id": "m1",
                },
            ),
            # text_end — missing required field 'message_id'
            ("text_end", {"schema_version": SCHEMA_VERSION_TEXT_END_RENDER_EVENT}),
            # text_chunk — missing required field 'delta'
            (
                "text_chunk",
                {
                    "schema_version": SCHEMA_VERSION_TEXT_CHUNK_RENDER_EVENT,
                    "message_id": "m1",
                },
            ),
            # run_started — missing required field 'run_id'
            (
                "run_started",
                {"schema_version": SCHEMA_VERSION_RUN_STARTED_RENDER_EVENT},
            ),
            # run_finished — missing required field 'run_id'
            (
                "run_finished",
                {"schema_version": SCHEMA_VERSION_RUN_FINISHED_RENDER_EVENT},
            ),
            # run_error — missing required fields 'run_id' and 'message'
            ("run_error", {"schema_version": SCHEMA_VERSION_RUN_ERROR_RENDER_EVENT}),
            # tool_call_start — missing required fields 'tool_call_id' + 'tool_name'
            (
                "tool_call_start",
                {"schema_version": SCHEMA_VERSION_TOOL_CALL_START_RENDER_EVENT},
            ),
            # tool_call_args — missing required field 'delta'
            (
                "tool_call_args",
                {
                    "schema_version": SCHEMA_VERSION_TOOL_CALL_ARGS_RENDER_EVENT,
                    "tool_call_id": "tc1",
                },
            ),
            # tool_call_end — missing required field 'tool_call_id'
            (
                "tool_call_end",
                {"schema_version": SCHEMA_VERSION_TOOL_CALL_END_RENDER_EVENT},
            ),
            # tool_call_result — missing required field 'content'
            (
                "tool_call_result",
                {
                    "schema_version": SCHEMA_VERSION_TOOL_CALL_RESULT_RENDER_EVENT,
                    "tool_call_id": "tc1",
                },
            ),
            # reasoning_start — missing required field 'message_id'
            (
                "reasoning_start",
                {"schema_version": SCHEMA_VERSION_REASONING_START_RENDER_EVENT},
            ),
            # reasoning_delta — missing required field 'delta'
            (
                "reasoning_delta",
                {
                    "schema_version": SCHEMA_VERSION_REASONING_DELTA_RENDER_EVENT,
                    "message_id": "m1",
                },
            ),
            # reasoning_end — missing required field 'message_id'
            (
                "reasoning_end",
                {"schema_version": SCHEMA_VERSION_REASONING_END_RENDER_EVENT},
            ),
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
    """decode_stream_events breaks on None event_type BEFORE the stream_error check."""
    import asyncio

    from lyra.adapters.nats.nats_stream_decoder import decode_stream_events

    q: asyncio.Queue[dict] = asyncio.Queue()
    await q.put({})

    events = []
    with caplog.at_level(
        logging.WARNING, logger="lyra.adapters.nats.nats_stream_decoder"
    ):
        async for event in decode_stream_events("test-stream", q):
            events.append(event)

    assert events == []

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
    """decode("stream_end", {}) returns None, no log.warning("unknown event_type")."""
    codec = NatsRenderEventCodec()
    with caplog.at_level(logging.WARNING, logger="lyra.nats.render_event_codec"):
        result_end = codec.decode("stream_end", {})
        result_error = codec.decode("stream_error", {})

    assert result_end is None
    assert result_error is None

    unknown_warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "unknown event_type" in r.getMessage()
    ]
    assert not unknown_warnings, (
        f"Unexpected 'unknown event_type' warning(s) for synthetic sentinels: "
        f"{[r.getMessage() for r in unknown_warnings]}"
    )
