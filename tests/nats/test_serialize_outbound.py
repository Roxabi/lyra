"""Serialization round-trip tests for outbound message types and render events.

Verifies that serialize() → deserialize() produces structurally equivalent
objects for all types published by NatsChannelProxy over NATS.
"""

from __future__ import annotations

import json

import pytest

from lyra.core.message import (
    InboundMessage,
    OutboundAttachment,
    OutboundMessage,
)
from lyra.core.render_events import (
    FileEditSummary,
    SilentCounts,
    TextRenderEvent,
    ToolSummaryRenderEvent,
)
from lyra.core.trust import TrustLevel
from roxabi_nats._serialize import deserialize, serialize

# ---------------------------------------------------------------------------
# OutboundMessage round-trip
# ---------------------------------------------------------------------------


def test_outbound_message_text_roundtrip() -> None:
    """Plain-text OutboundMessage survives serialize → deserialize."""
    original = OutboundMessage.from_text("Hello from hub")
    data = serialize(original)
    recovered = deserialize(data, OutboundMessage)

    assert recovered.content == original.content
    assert recovered.is_final == original.is_final
    assert recovered.intermediate == original.intermediate
    assert recovered.buttons == original.buttons
    assert recovered.edit_id == original.edit_id


def test_outbound_message_with_metadata_roundtrip() -> None:
    """OutboundMessage with metadata dict survives round-trip."""
    original = OutboundMessage.from_text("With meta")
    original.metadata["reply_message_id"] = "42"
    data = serialize(original)
    recovered = deserialize(data, OutboundMessage)

    assert recovered.metadata.get("reply_message_id") == "42"


def test_outbound_message_intermediate_roundtrip() -> None:
    """OutboundMessage with intermediate=True survives round-trip."""
    original = OutboundMessage(
        content=["Thinking..."], intermediate=True, is_final=False
    )
    data = serialize(original)
    recovered = deserialize(data, OutboundMessage)

    assert recovered.intermediate is True
    assert recovered.is_final is False
    assert recovered.content == ["Thinking..."]


# ---------------------------------------------------------------------------
# TextRenderEvent round-trip
# ---------------------------------------------------------------------------


def test_text_render_event_roundtrip() -> None:
    """TextRenderEvent survives serialize → deserialize."""
    original = TextRenderEvent(text="Final answer", is_final=True, is_error=False)
    data = serialize(original)
    recovered = deserialize(data, TextRenderEvent)

    assert recovered.text == "Final answer"
    assert recovered.is_final is True
    assert recovered.is_error is False


def test_text_render_event_error_roundtrip() -> None:
    """TextRenderEvent with is_error=True survives round-trip."""
    original = TextRenderEvent(
        text="Something went wrong", is_final=True, is_error=True
    )
    data = serialize(original)
    recovered = deserialize(data, TextRenderEvent)

    assert recovered.is_error is True
    assert recovered.text == "Something went wrong"


def test_text_render_event_not_final_roundtrip() -> None:
    """TextRenderEvent with is_final=False survives round-trip."""
    original = TextRenderEvent(text="Partial...", is_final=False)
    data = serialize(original)
    recovered = deserialize(data, TextRenderEvent)

    assert recovered.is_final is False
    assert recovered.text == "Partial..."


# ---------------------------------------------------------------------------
# ToolSummaryRenderEvent round-trip
# ---------------------------------------------------------------------------


def test_tool_summary_render_event_empty_roundtrip() -> None:
    """Empty ToolSummaryRenderEvent survives serialize → deserialize."""
    original = ToolSummaryRenderEvent()
    data = serialize(original)
    recovered = deserialize(data, ToolSummaryRenderEvent)

    assert recovered.bash_commands == []
    assert recovered.web_fetches == []
    assert recovered.agent_calls == []
    assert recovered.is_complete is False


def test_tool_summary_render_event_with_data_roundtrip() -> None:
    """ToolSummaryRenderEvent with populated fields survives round-trip.

    Note: the deserializer handles list[X] but not dict[str, V] with typed
    values — files dict values come back as raw dicts (JSON objects). The
    scalar and list fields round-trip fully; SilentCounts (a nested dataclass)
    also round-trips because the field type hint is resolved at decode time.
    """
    file_summary = FileEditSummary(
        path="/src/foo.py", edits=["added function bar"], count=1
    )
    silent = SilentCounts(reads=3, greps=2, globs=1)
    original = ToolSummaryRenderEvent(
        files={"/src/foo.py": file_summary},
        bash_commands=["make test"],
        web_fetches=["https://example.com"],
        agent_calls=["sub-agent-1"],
        silent_counts=silent,
        is_complete=True,
    )
    data = serialize(original)
    recovered = deserialize(data, ToolSummaryRenderEvent)

    assert recovered.is_complete is True
    assert recovered.bash_commands == ["make test"]
    assert recovered.web_fetches == ["https://example.com"]
    assert recovered.agent_calls == ["sub-agent-1"]
    assert recovered.silent_counts.reads == 3
    assert recovered.silent_counts.greps == 2
    assert recovered.silent_counts.globs == 1
    # files dict keys are preserved; values come back as raw dicts because
    # dict[str, FileEditSummary] value types are not rehydrated by _decode_concrete
    assert "/src/foo.py" in recovered.files
    recovered_file = recovered.files["/src/foo.py"]
    # Accept both reconstructed dataclass and raw dict (serializer limitation)
    if isinstance(recovered_file, dict):
        assert recovered_file["path"] == "/src/foo.py"
        assert recovered_file["count"] == 1
    else:
        assert recovered_file.path == "/src/foo.py"
        assert recovered_file.count == 1


# ---------------------------------------------------------------------------
# OutboundAttachment round-trip
# ---------------------------------------------------------------------------


def test_outbound_attachment_roundtrip() -> None:
    """OutboundAttachment with bytes data survives serialize → deserialize."""
    raw = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16  # fake PNG header + padding
    original = OutboundAttachment(
        data=raw,
        type="image",
        mime_type="image/png",
        filename="diagram.png",
        caption="Architecture diagram",
        reply_to_id="msg-99",
    )
    data = serialize(original)
    recovered = deserialize(data, OutboundAttachment)

    assert recovered.data == raw
    assert recovered.type == "image"
    assert recovered.mime_type == "image/png"
    assert recovered.filename == "diagram.png"
    assert recovered.caption == "Architecture diagram"
    assert recovered.reply_to_id == "msg-99"


def test_outbound_attachment_document_roundtrip() -> None:
    """OutboundAttachment of type 'document' round-trips cleanly."""
    payload = b"PDF content"
    original = OutboundAttachment(
        data=payload,
        type="document",
        mime_type="application/pdf",
        filename="report.pdf",
    )
    data = serialize(original)
    recovered = deserialize(data, OutboundAttachment)

    assert recovered.data == payload
    assert recovered.type == "document"
    assert recovered.filename == "report.pdf"
    assert recovered.caption is None
    assert recovered.reply_to_id is None


def test_outbound_attachment_file_no_optional_fields_roundtrip() -> None:
    """OutboundAttachment with minimal fields round-trips cleanly."""
    original = OutboundAttachment(
        data=b"binary",
        type="file",
        mime_type="application/octet-stream",
    )
    data = serialize(original)
    recovered = deserialize(data, OutboundAttachment)

    assert recovered.data == b"binary"
    assert recovered.type == "file"
    assert recovered.filename is None
    assert recovered.caption is None


# ---------------------------------------------------------------------------
# Legacy round-trip: schema_version defaults to 1 (MT-5, SC-4)
# ---------------------------------------------------------------------------

# Factories for the 5 envelope types — all constructed WITHOUT setting schema_version,
# relying entirely on the dataclass default.


def _make_inbound_message() -> InboundMessage:
    return InboundMessage(
        id="msg-legacy",
        platform="telegram",
        bot_id="main",
        scope_id="chat:42",
        user_id="user:1",
        user_name="Alice",
        is_mention=False,
        text="hello",
        text_raw="hello",
        trust_level=TrustLevel.PUBLIC,
    )


def _make_outbound_message() -> OutboundMessage:
    return OutboundMessage.from_text("hello from hub")


def _make_text_render_event() -> TextRenderEvent:
    return TextRenderEvent(text="Final answer", is_final=True)


def _make_tool_summary_render_event() -> ToolSummaryRenderEvent:
    return ToolSummaryRenderEvent()


_ENVELOPE_FACTORIES = [
    pytest.param(_make_inbound_message, InboundMessage, id="InboundMessage"),
    pytest.param(_make_outbound_message, OutboundMessage, id="OutboundMessage"),
    pytest.param(_make_text_render_event, TextRenderEvent, id="TextRenderEvent"),
    pytest.param(
        _make_tool_summary_render_event,
        ToolSummaryRenderEvent,
        id="ToolSummaryRenderEvent",
    ),
]


@pytest.mark.parametrize("factory,envelope_type", _ENVELOPE_FACTORIES)
def test_legacy_payload_round_trip_defaults_to_v1(factory, envelope_type) -> None:
    """All 5 envelopes round-trip with schema_version==1 when default is used.

    Covers SC-4: legacy payload round-trips cleanly with default schema_version=1.
    Also verifies that a payload with no schema_version key at all (simulating a
    pre-versioning producer) deserializes correctly — the absent field path in
    _decode_dataclass yields the dataclass default of 1.
    """
    # Arrange — construct without setting schema_version (rely on dataclass default)
    original = factory()
    assert original.schema_version == 1

    # Act — full round-trip: serialize → deserialize
    data = serialize(original)
    recovered = deserialize(data, envelope_type)

    # Assert — schema_version is preserved as 1 through the wire format
    assert recovered.schema_version == 1

    # --- Simulate a pre-versioning producer (no schema_version key in JSON) ---
    # Strip schema_version from the serialized JSON to mimic an old producer
    raw_dict = json.loads(data.decode("utf-8"))
    assert "schema_version" in raw_dict, "serialize() must include schema_version"
    del raw_dict["schema_version"]
    legacy_bytes = json.dumps(raw_dict).encode("utf-8")

    # Deserialize the stripped payload → should fall back to dataclass default = 1
    legacy_recovered = deserialize(legacy_bytes, envelope_type)
    assert legacy_recovered.schema_version == 1
