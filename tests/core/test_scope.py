"""Tests for lyra.core.scope — user_scoped() helper (#356)."""

from __future__ import annotations

from lyra.core.scope import user_scoped


def test_user_scoped_appends_user_id() -> None:
    """Basic case: chat scope + Telegram user."""
    assert user_scoped("chat:42", "tg:user:1") == "chat:42:user:tg:user:1"


def test_user_scoped_with_topic() -> None:
    """Forum topic scope gets user suffix after topic."""
    assert (
        user_scoped("chat:42:topic:7", "tg:user:1") == "chat:42:topic:7:user:tg:user:1"
    )


def test_user_scoped_discord_channel() -> None:
    """Discord channel scope + Discord user."""
    assert user_scoped("channel:333", "dc:user:42") == "channel:333:user:dc:user:42"
