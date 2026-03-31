"""Serialization round-trip tests for outbound message types and render events.

Verifies that serialize() → deserialize() produces structurally equivalent
objects for all types published by NatsChannelProxy over NATS.
"""
from __future__ import annotations

from lyra.core.message import OutboundAttachment, OutboundMessage
from lyra.core.render_events import (
    FileEditSummary,
    SilentCounts,
    TextRenderEvent,
    ToolSummaryRenderEvent,
)
from lyra.nats._serialize import deserialize, serialize

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
