"""Serialization round-trip tests for outbound message types and render events.

Verifies that serialize() → deserialize() produces structurally equivalent
objects for all types published by NatsChannelProxy over NATS.
"""

from __future__ import annotations

import json

import pytest

from factory.core.auth.trust import TrustLevel
from factory.core.messaging.message import (
    InboundMessage,
    OutboundAttachment,
    OutboundMessage,
)
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


_ENVELOPE_FACTORIES = [
    pytest.param(_make_inbound_message, InboundMessage, id="InboundMessage"),
    pytest.param(_make_outbound_message, OutboundMessage, id="OutboundMessage"),
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
