"""Tests for TelegramAdapter.normalize() field extraction and mention logic.

Covers: T3, T4, T8, T11, T11c, reply_to_id extraction.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from lyra.adapters.telegram import _ALLOW_ALL
from lyra.core.message import InboundMessage

# ---------------------------------------------------------------------------
# T3 — _normalize() builds correct TelegramContext for private chat
# ---------------------------------------------------------------------------


def test_normalize_private_chat_context() -> None:
    """normalize() on a private-chat message produces correct platform_meta."""
    from lyra.adapters.telegram import TelegramAdapter  # ImportError expected in RED

    hub = MagicMock()
    adapter = TelegramAdapter(
        bot_id="main", token="test-token-secret", hub=hub, auth=_ALLOW_ALL
    )

    aiogram_msg = SimpleNamespace(
        chat=SimpleNamespace(id=123, type="private"),
        from_user=SimpleNamespace(id=42, full_name="Alice", is_bot=False),
        text="hello",
        date=datetime.now(timezone.utc),
        message_thread_id=None,
        message_id=99,
        entities=None,
    )

    msg = adapter.normalize(aiogram_msg)

    assert isinstance(msg, InboundMessage)
    assert msg.platform == "telegram"
    assert msg.scope_id == "chat:123"
    assert msg.text == "hello"
    assert msg.user_id == "tg:user:42"
    assert msg.platform_meta["chat_id"] == 123
    assert msg.platform_meta["topic_id"] is None
    assert msg.platform_meta["is_group"] is False
    assert msg.platform_meta["message_id"] == 99


# ---------------------------------------------------------------------------
# T4 — is_mention logic
# ---------------------------------------------------------------------------


def test_is_mention_false_in_private_chat() -> None:
    """Private chat → is_mention=False regardless of entities."""
    from lyra.adapters.telegram import TelegramAdapter  # ImportError expected in RED

    hub = MagicMock()
    adapter = TelegramAdapter(
        bot_id="main", token="test-token-secret", hub=hub, auth=_ALLOW_ALL
    )

    aiogram_msg = SimpleNamespace(
        chat=SimpleNamespace(id=123, type="private"),
        from_user=SimpleNamespace(id=42, full_name="Alice", is_bot=False),
        text="hello",
        date=datetime.now(timezone.utc),
        message_thread_id=None,
        entities=None,
    )

    msg = adapter.normalize(aiogram_msg)

    assert msg.is_mention is False


def test_is_mention_true_when_entity_at_offset_zero() -> None:
    """Group chat with @mention entity at offset 0 matching bot username → True."""
    from lyra.adapters.telegram import TelegramAdapter  # ImportError expected in RED

    hub = MagicMock()
    adapter = TelegramAdapter(
        bot_id="main", token="test-token-secret", hub=hub, auth=_ALLOW_ALL
    )
    adapter._bot_username = "lyra_bot"  # simulate resolve_identity()

    entity = SimpleNamespace(type="mention", offset=0, length=9)  # "@lyra_bot"
    aiogram_msg = SimpleNamespace(
        chat=SimpleNamespace(id=456, type="group"),
        from_user=SimpleNamespace(id=42, full_name="Bob", is_bot=False),
        text="@lyra_bot hello",
        date=datetime.now(timezone.utc),
        message_thread_id=None,
        entities=[entity],
    )

    msg = adapter.normalize(aiogram_msg)

    assert msg.is_mention is True
    assert msg.text == "hello"  # @mention stripped


# ---------------------------------------------------------------------------
# T8 — Bot token must not appear in log output
# ---------------------------------------------------------------------------


def test_token_not_in_logs(caplog: pytest.LogCaptureFixture) -> None:
    """After _normalize(), no log record contains the bot token string."""
    from lyra.adapters.telegram import TelegramAdapter  # ImportError expected in RED

    hub = MagicMock()
    adapter = TelegramAdapter(
        bot_id="main", token="test-token-secret", hub=hub, auth=_ALLOW_ALL
    )

    aiogram_msg = SimpleNamespace(
        chat=SimpleNamespace(id=123, type="private"),
        from_user=SimpleNamespace(id=42, full_name="Alice", is_bot=False),
        text="hello",
        date=datetime.now(timezone.utc),
        message_thread_id=None,
        entities=None,
    )

    with caplog.at_level(logging.DEBUG, logger="lyra.adapters.telegram"):
        adapter.normalize(aiogram_msg)

    for record in caplog.records:
        assert "test-token-secret" not in record.getMessage()


# ---------------------------------------------------------------------------
# T11 — _normalize() captures message_id from incoming Telegram message
# ---------------------------------------------------------------------------


def test_normalize_captures_message_id() -> None:
    """normalize() captures message_id in platform_meta."""
    from lyra.adapters.telegram import TelegramAdapter

    # Arrange
    hub = MagicMock()
    adapter = TelegramAdapter(
        bot_id="main", token="test-token-secret", hub=hub, auth=_ALLOW_ALL
    )
    aiogram_msg = SimpleNamespace(
        chat=SimpleNamespace(id=123, type="private"),
        from_user=SimpleNamespace(id=42, full_name="Alice", is_bot=False),
        text="hello",
        date=datetime.now(timezone.utc),
        message_thread_id=None,
        message_id=777,
        entities=None,
    )

    # Act
    msg = adapter.normalize(aiogram_msg)

    # Assert
    assert isinstance(msg, InboundMessage)
    assert msg.platform_meta["message_id"] == 777


def test_normalize_message_id_none_when_absent() -> None:
    """normalize() sets platform_meta message_id=None when message_id absent.

    Note: real aiogram Message objects always have message_id (required Bot API field).
    This test exercises the getattr defensive fallback used by SimpleNamespace stubs.
    """
    from lyra.adapters.telegram import TelegramAdapter

    # Arrange
    hub = MagicMock()
    adapter = TelegramAdapter(
        bot_id="main", token="test-token-secret", hub=hub, auth=_ALLOW_ALL
    )
    aiogram_msg = SimpleNamespace(
        chat=SimpleNamespace(id=123, type="private"),
        from_user=SimpleNamespace(id=42, full_name="Alice", is_bot=False),
        text="hello",
        date=datetime.now(timezone.utc),
        message_thread_id=None,
        # no message_id attribute — exercises getattr(..., None) defensive path
        entities=None,
    )

    # Act
    msg = adapter.normalize(aiogram_msg)

    # Assert
    assert isinstance(msg, InboundMessage)
    assert msg.platform_meta["message_id"] is None


# ---------------------------------------------------------------------------
# T11c — _normalize() captures both topic_id and message_id for group/forum
# ---------------------------------------------------------------------------


def test_normalize_captures_topic_and_message_id_for_forum() -> None:
    """Forum supergroup: both topic_id and message_id captured simultaneously."""
    from lyra.adapters.telegram import TelegramAdapter

    # Arrange
    hub = MagicMock()
    adapter = TelegramAdapter(
        bot_id="main", token="test-token-secret", hub=hub, auth=_ALLOW_ALL
    )
    aiogram_msg = SimpleNamespace(
        chat=SimpleNamespace(id=456, type="supergroup"),
        from_user=SimpleNamespace(id=42, full_name="Alice", is_bot=False),
        text="hello forum",
        date=datetime.now(timezone.utc),
        message_thread_id=99,
        message_id=777,
        entities=None,
    )

    # Act
    msg = adapter.normalize(aiogram_msg)

    # Assert
    assert isinstance(msg, InboundMessage)
    assert msg.platform_meta["topic_id"] == 99
    assert msg.platform_meta["message_id"] == 777
    assert msg.platform_meta["is_group"] is True
    assert msg.scope_id == "chat:456:topic:99:user:tg:user:42"


def test_normalize_empty_text() -> None:
    """normalize() with text=None produces msg.text == \"\"."""
    from lyra.adapters.telegram import TelegramAdapter

    hub = MagicMock()
    adapter = TelegramAdapter(
        bot_id="main", token="test-token-secret", hub=hub, auth=_ALLOW_ALL
    )
    aiogram_msg = SimpleNamespace(
        chat=SimpleNamespace(id=123, type="private"),
        from_user=SimpleNamespace(id=42, full_name="Alice", is_bot=False),
        text=None,
        date=datetime.now(timezone.utc),
        message_thread_id=None,
        message_id=99,
        entities=None,
    )
    msg = adapter.normalize(aiogram_msg)
    assert msg.text == ""


# ---------------------------------------------------------------------------
# reply_to_id extraction in normalize()
# ---------------------------------------------------------------------------


def test_normalize_sets_reply_to_id_when_reply_present() -> None:
    """normalize() sets reply_to_id from raw.reply_to_message.message_id."""
    from types import SimpleNamespace

    from lyra.adapters.telegram import TelegramAdapter

    hub = MagicMock()
    adapter = TelegramAdapter(
        bot_id="main", token="test-token-secret", hub=hub, auth=_ALLOW_ALL
    )
    reply_msg = SimpleNamespace(message_id=77)
    aiogram_msg = SimpleNamespace(
        chat=SimpleNamespace(id=123, type="private"),
        from_user=SimpleNamespace(id=42, full_name="Alice", is_bot=False),
        text="reply here",
        date=datetime.now(timezone.utc),
        message_thread_id=None,
        message_id=88,
        entities=None,
        reply_to_message=reply_msg,
    )

    msg = adapter.normalize(aiogram_msg)

    assert msg.reply_to_id == "77"


def test_normalize_reply_to_id_none_when_no_reply() -> None:
    """normalize() sets reply_to_id to None when raw.reply_to_message is absent."""
    from lyra.adapters.telegram import TelegramAdapter

    hub = MagicMock()
    adapter = TelegramAdapter(
        bot_id="main", token="test-token-secret", hub=hub, auth=_ALLOW_ALL
    )
    aiogram_msg = SimpleNamespace(
        chat=SimpleNamespace(id=123, type="private"),
        from_user=SimpleNamespace(id=42, full_name="Alice", is_bot=False),
        text="no reply",
        date=datetime.now(timezone.utc),
        message_thread_id=None,
        message_id=88,
        entities=None,
        reply_to_message=None,
    )

    msg = adapter.normalize(aiogram_msg)

    assert msg.reply_to_id is None


# ---------------------------------------------------------------------------
# #356 — user-scoped scope_id in shared spaces
# ---------------------------------------------------------------------------


def test_normalize_group_chat_user_scoped_scope_id() -> None:
    """Group chat → scope_id includes user_id suffix."""
    from lyra.adapters.telegram import TelegramAdapter

    hub = MagicMock()
    adapter = TelegramAdapter(
        bot_id="main", token="test-token-secret", hub=hub, auth=_ALLOW_ALL
    )
    adapter._bot_username = "lyra_bot"

    entity = SimpleNamespace(type="mention", offset=0, length=9)
    aiogram_msg = SimpleNamespace(
        chat=SimpleNamespace(id=456, type="group"),
        from_user=SimpleNamespace(id=42, full_name="Alice", is_bot=False),
        text="@lyra_bot hello",
        date=datetime.now(timezone.utc),
        message_thread_id=None,
        message_id=99,
        entities=[entity],
    )

    msg = adapter.normalize(aiogram_msg)

    assert msg.scope_id == "chat:456:user:tg:user:42"
    assert msg.user_id == "tg:user:42"


def test_normalize_group_chat_no_mention_still_user_scoped() -> None:
    """Group chat without @mention → scope_id still includes user_id suffix."""
    from lyra.adapters.telegram import TelegramAdapter

    hub = MagicMock()
    adapter = TelegramAdapter(
        bot_id="main", token="test-token-secret", hub=hub, auth=_ALLOW_ALL
    )

    aiogram_msg = SimpleNamespace(
        chat=SimpleNamespace(id=456, type="group"),
        from_user=SimpleNamespace(id=42, full_name="Alice", is_bot=False),
        text="hello everyone",
        date=datetime.now(timezone.utc),
        message_thread_id=None,
        message_id=99,
        entities=None,
    )

    msg = adapter.normalize(aiogram_msg)

    assert msg.scope_id == "chat:456:user:tg:user:42"
    assert msg.is_mention is False


def test_normalize_forum_topic_user_scoped_scope_id() -> None:
    """Forum topic in supergroup → scope_id includes topic AND user_id suffix."""
    from lyra.adapters.telegram import TelegramAdapter

    hub = MagicMock()
    adapter = TelegramAdapter(
        bot_id="main", token="test-token-secret", hub=hub, auth=_ALLOW_ALL
    )
    adapter._bot_username = "lyra_bot"

    entity = SimpleNamespace(type="mention", offset=0, length=9)
    aiogram_msg = SimpleNamespace(
        chat=SimpleNamespace(id=456, type="supergroup"),
        from_user=SimpleNamespace(id=42, full_name="Alice", is_bot=False),
        text="@lyra_bot hello",
        date=datetime.now(timezone.utc),
        message_thread_id=7,
        message_id=99,
        entities=[entity],
    )

    msg = adapter.normalize(aiogram_msg)

    assert msg.scope_id == "chat:456:topic:7:user:tg:user:42"


def test_normalize_private_chat_scope_id_unchanged() -> None:
    """Private chat → scope_id has no user suffix (regression)."""
    from lyra.adapters.telegram import TelegramAdapter

    hub = MagicMock()
    adapter = TelegramAdapter(
        bot_id="main", token="test-token-secret", hub=hub, auth=_ALLOW_ALL
    )

    aiogram_msg = SimpleNamespace(
        chat=SimpleNamespace(id=123, type="private"),
        from_user=SimpleNamespace(id=42, full_name="Alice", is_bot=False),
        text="hello",
        date=datetime.now(timezone.utc),
        message_thread_id=None,
        message_id=99,
        entities=None,
    )

    msg = adapter.normalize(aiogram_msg)

    assert msg.scope_id == "chat:123"


def test_two_users_same_group_get_distinct_pool_ids() -> None:
    """Two users in the same group → distinct scope_ids → distinct pool_ids."""
    from lyra.adapters.telegram import TelegramAdapter
    from lyra.core.hub.hub_protocol import RoutingKey
    from lyra.core.message import Platform

    hub = MagicMock()
    adapter = TelegramAdapter(
        bot_id="main", token="test-token-secret", hub=hub, auth=_ALLOW_ALL
    )
    adapter._bot_username = "lyra_bot"

    def _make_group_msg(user_id: int, user_name: str) -> object:
        entity = SimpleNamespace(type="mention", offset=0, length=9)
        return SimpleNamespace(
            chat=SimpleNamespace(id=456, type="group"),
            from_user=SimpleNamespace(id=user_id, full_name=user_name, is_bot=False),
            text="@lyra_bot hi",
            date=datetime.now(timezone.utc),
            message_thread_id=None,
            message_id=99,
            entities=[entity],
        )

    msg_alice = adapter.normalize(_make_group_msg(1, "Alice"))
    msg_bob = adapter.normalize(_make_group_msg(2, "Bob"))

    assert msg_alice.scope_id != msg_bob.scope_id

    key_alice = RoutingKey(Platform.TELEGRAM, "main", msg_alice.scope_id)
    key_bob = RoutingKey(Platform.TELEGRAM, "main", msg_bob.scope_id)
    assert key_alice.to_pool_id() != key_bob.to_pool_id()
