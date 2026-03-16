"""Tests for CLIAdapter (S3)."""

from __future__ import annotations

from lyra.adapters.cli import CLIAdapter
from lyra.core.message import InboundMessage
from lyra.core.trust import TrustLevel


def test_on_input_returns_inbound_message() -> None:
    """on_input() returns an InboundMessage instance."""
    adapter = CLIAdapter()
    msg = adapter.on_input("hello")
    assert isinstance(msg, InboundMessage)


def test_on_input_returns_owner_trust() -> None:
    """on_input() always produces trust_level=OWNER."""
    adapter = CLIAdapter()
    msg = adapter.on_input("hello")
    assert msg.trust_level == TrustLevel.OWNER


def test_on_input_text_passed_through() -> None:
    """on_input() preserves text and text_raw exactly."""
    adapter = CLIAdapter()
    msg = adapter.on_input("my input text")
    assert msg.text == "my input text"
    assert msg.text_raw == "my input text"


def test_on_input_platform_is_cli() -> None:
    """on_input() produces platform='cli'."""
    adapter = CLIAdapter()
    msg = adapter.on_input("hi")
    assert msg.platform == "cli"


def test_on_input_user_id() -> None:
    """on_input() uses the canonical CLI user_id."""
    adapter = CLIAdapter()
    msg = adapter.on_input("hi")
    assert msg.user_id == "cli:user:local"


def test_on_input_unique_ids() -> None:
    """Successive calls to on_input() produce distinct message IDs."""
    adapter = CLIAdapter()
    msg1 = adapter.on_input("a")
    msg2 = adapter.on_input("b")
    assert msg1.id != msg2.id


def test_on_input_empty_string() -> None:
    """on_input('') produces an InboundMessage with empty text and OWNER trust."""
    adapter = CLIAdapter()
    msg = adapter.on_input("")
    assert msg.text == ""
    assert msg.text_raw == ""
    assert msg.trust_level == TrustLevel.OWNER
