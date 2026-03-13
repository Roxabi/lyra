"""Tests for OutboundAttachment and OutboundMessage.attachments (#143)."""

from __future__ import annotations

from lyra.core.message import OutboundAttachment, OutboundMessage


def test_outbound_attachment_importable() -> None:
    """OutboundAttachment must be importable from lyra.core.message."""
    from lyra.core.message import OutboundAttachment  # noqa: F811, F401


def test_outbound_attachment_from_bytes() -> None:
    """OutboundAttachment can be constructed with raw bytes."""
    att = OutboundAttachment(
        bytes_or_path=b"fake-png-data",
        file_name="photo.png",
        mime_type="image/png",
        caption="A photo",
    )
    assert att.bytes_or_path == b"fake-png-data"
    assert att.file_name == "photo.png"
    assert att.mime_type == "image/png"
    assert att.caption == "A photo"


def test_outbound_attachment_from_path() -> None:
    """OutboundAttachment can be constructed with a file path string."""
    att = OutboundAttachment(
        bytes_or_path="/tmp/test.pdf",
        file_name="test.pdf",
        mime_type="application/pdf",
    )
    assert att.bytes_or_path == "/tmp/test.pdf"
    assert att.caption is None


def test_outbound_attachment_defaults() -> None:
    """mime_type defaults to application/octet-stream, caption to None."""
    att = OutboundAttachment(bytes_or_path=b"data", file_name="file.bin")
    assert att.mime_type == "application/octet-stream"
    assert att.caption is None


def test_outbound_message_attachments_default_empty() -> None:
    """OutboundMessage.attachments defaults to an empty list."""
    msg = OutboundMessage.from_text("hello")
    assert msg.attachments == []


def test_outbound_message_with_attachments() -> None:
    """OutboundMessage can carry a list of OutboundAttachment."""
    att = OutboundAttachment(
        bytes_or_path=b"data",
        file_name="doc.pdf",
        mime_type="application/pdf",
    )
    msg = OutboundMessage(content=["See attached"], attachments=[att])
    assert len(msg.attachments) == 1
    assert msg.attachments[0].file_name == "doc.pdf"


def test_outbound_attachment_frozen() -> None:
    """OutboundAttachment is frozen (immutable)."""
    att = OutboundAttachment(bytes_or_path=b"x", file_name="f.txt")
    import dataclasses

    assert dataclasses.fields(att)  # is a dataclass
    try:
        att.file_name = "other.txt"  # type: ignore[misc]
        assert False, "Should have raised FrozenInstanceError"
    except (AttributeError, dataclasses.FrozenInstanceError):
        pass
