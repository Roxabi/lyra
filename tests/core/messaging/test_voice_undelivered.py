"""Tests for voice_undelivered notification primitives (#1482).

Validates:
- VOICE_UNDELIVERED_MSG constant presence and tone.
- notify_undelivered() returns an OutboundMessage with the expected text.
- No str(exc) leakage — context kwarg is NOT embedded in the returned payload.
- notify_undelivered is importable from both factory.core.messaging and
  factory.core.messaging.voice_notify (canonical path).
- The returned OutboundMessage is usable via OutboundMessage.to_text().
"""

from __future__ import annotations

from factory.core.messaging.message import OutboundMessage
from factory.core.messaging.voice_notify import (
    VOICE_UNDELIVERED_MSG,
    notify_undelivered,
)

# ---------------------------------------------------------------------------
# Constant
# ---------------------------------------------------------------------------


def test_voice_undelivered_msg_is_nonempty_string() -> None:
    """VOICE_UNDELIVERED_MSG must be a non-empty string."""
    assert isinstance(VOICE_UNDELIVERED_MSG, str)
    assert len(VOICE_UNDELIVERED_MSG) > 0


def test_voice_undelivered_msg_contains_warning_emoji() -> None:
    """VOICE_UNDELIVERED_MSG tone must match the ⚠️ prefix from tts_dispatch."""
    assert VOICE_UNDELIVERED_MSG.startswith("⚠️")


def test_voice_undelivered_msg_mentions_voice_or_audio() -> None:
    """VOICE_UNDELIVERED_MSG must clearly refer to voice/audio content."""
    lower = VOICE_UNDELIVERED_MSG.lower()
    assert "voice" in lower or "audio" in lower


# ---------------------------------------------------------------------------
# notify_undelivered — return type and content
# ---------------------------------------------------------------------------


def test_notify_undelivered_returns_outbound_message() -> None:
    """notify_undelivered() must return an OutboundMessage instance."""
    result = notify_undelivered()
    assert isinstance(result, OutboundMessage)


def test_notify_undelivered_text_matches_constant() -> None:
    """notify_undelivered().to_text() must equal VOICE_UNDELIVERED_MSG."""
    result = notify_undelivered()
    assert result.to_text() == VOICE_UNDELIVERED_MSG


def test_notify_undelivered_with_context_text_still_matches_constant() -> None:
    """context kwarg must NOT alter the user-facing text (no str(exc) leakage)."""
    result = notify_undelivered(context="term-retry-exceeded")
    assert result.to_text() == VOICE_UNDELIVERED_MSG


def test_notify_undelivered_context_not_in_payload() -> None:
    """context value must never appear in the returned OutboundMessage."""
    sentinel = "super-secret-host:4222-token=AKIA"
    result = notify_undelivered(context=sentinel)
    # Inspect all content parts
    full_text = result.to_text()
    assert sentinel not in full_text
    # Also verify via raw content list
    assert all(sentinel not in str(part) for part in result.content)


def test_notify_undelivered_no_context_does_not_raise() -> None:
    """notify_undelivered() with no arguments must succeed."""
    result = notify_undelivered()
    assert result is not None


# ---------------------------------------------------------------------------
# Package-level re-export
# ---------------------------------------------------------------------------


def test_importable_from_lyra_core_messaging() -> None:
    """VOICE_UNDELIVERED_MSG and notify_undelivered must be re-exported from
    factory.core.messaging so consumers can use the short import path."""
    from factory.core.messaging import (  # noqa: PLC0415
        VOICE_UNDELIVERED_MSG as MSG,
    )
    from factory.core.messaging import (
        notify_undelivered as fn,
    )

    assert MSG is VOICE_UNDELIVERED_MSG
    assert fn is notify_undelivered
