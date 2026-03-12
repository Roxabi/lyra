"""RED-phase tests for OutboundMessage and supporting types (issue #138, Slice V1).

All tests in this module are expected to FAIL until the backend-dev GREEN phase
implements OutboundMessage, Button, CodeBlock, Attachment, ContentPart in
lyra.core.message and exports them from lyra.core.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Slice V1 — Core types importable
# ---------------------------------------------------------------------------


def test_outbound_message_importable() -> None:
    """OutboundMessage, Button, CodeBlock, Attachment, ContentPart must be
    importable from lyra.core.message."""
    # Arrange / Act / Assert — ImportError is the expected RED failure
    from lyra.core.message import (  # noqa: F401
        Attachment,
        Button,
        CodeBlock,
        ContentPart,
        OutboundMessage,
    )


def test_from_text_factory() -> None:
    """OutboundMessage.from_text('hello world') returns the correct shape."""
    from lyra.core.message import OutboundMessage

    # Arrange
    text = "hello world"

    # Act
    msg = OutboundMessage.from_text(text)

    # Assert
    assert msg.content == ["hello world"]
    assert msg.buttons == []
    assert msg.edit_id is None
    assert msg.is_final is True


def test_response_to_outbound() -> None:
    """Response(content='x').to_outbound() returns OutboundMessage with content=['x'].
    """
    from lyra.core.message import OutboundMessage, Response

    # Arrange
    response = Response(content="x")

    # Act
    outbound = response.to_outbound()

    # Assert
    assert isinstance(outbound, OutboundMessage)
    assert outbound.content == ["x"]


def test_to_text_renders_code_block() -> None:
    """OutboundMessage.to_text() renders CodeBlock as a fenced code block string."""
    from lyra.core.message import CodeBlock, OutboundMessage

    # Arrange
    outbound = OutboundMessage(content=[CodeBlock(code="x = 1", language="python")])

    # Act
    text = outbound.to_text()

    # Assert
    assert "```python" in text
    assert "x = 1" in text


def test_to_text_renders_attachment() -> None:
    """OutboundMessage.to_text() renders Attachment as 'url — caption'."""
    from lyra.core.message import Attachment, OutboundMessage

    # Arrange
    outbound = OutboundMessage(
        content=[
            Attachment(url="http://example/img", media_type="image/png", caption="pic")
        ]
    )

    # Act
    text = outbound.to_text()

    # Assert
    assert "http://example/img" in text
    assert "pic" in text


def test_to_text_multi_part() -> None:
    """OutboundMessage.to_text() joins multiple content parts with newlines."""
    from lyra.core.message import CodeBlock, OutboundMessage

    # Arrange
    outbound = OutboundMessage(
        content=["hello", CodeBlock(code="y = 2", language=None)]
    )

    # Act
    text = outbound.to_text()

    # Assert
    assert "hello" in text
    assert "y = 2" in text
    assert "\n" in text  # parts joined by newline


def test_outbound_importable_from_core() -> None:
    """OutboundMessage, Button, CodeBlock, Attachment, ContentPart must be
    importable from lyra.core (the package __init__ re-exports)."""
    from lyra.core import (  # noqa: F401
        Attachment,
        Button,
        CodeBlock,
        ContentPart,
        OutboundMessage,
    )
