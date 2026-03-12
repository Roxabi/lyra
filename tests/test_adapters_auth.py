"""Integration tests for adapter auth gates (issue #151).

RED phase — these tests are expected to FAIL until the GREEN phase:
  - adds AuthMiddleware injection + BLOCKED guard to TelegramAdapter (S4)
  - adds AuthMiddleware injection + BLOCKED guard to DiscordAdapter (S5)
  - creates CLIAdapter stub (S3)

Spec trace: SC-7, SC-8, SC-9, SC-10, SC-13, SC-14
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lyra.core.auth import AuthMiddleware, TrustLevel

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def blocked_auth() -> AuthMiddleware:
    """AuthMiddleware that blocks every user."""
    return AuthMiddleware({}, TrustLevel.BLOCKED)


@pytest.fixture
def owner_auth() -> AuthMiddleware:
    """AuthMiddleware that gives tg:user:1 OWNER, everyone else BLOCKED."""
    return AuthMiddleware({"tg:user:1": TrustLevel.OWNER}, TrustLevel.BLOCKED)


@pytest.fixture
def discord_owner_auth() -> AuthMiddleware:
    """AuthMiddleware that gives dc:user:42 OWNER, everyone else BLOCKED."""
    return AuthMiddleware({"dc:user:42": TrustLevel.OWNER}, TrustLevel.BLOCKED)


@pytest.fixture
def public_auth() -> AuthMiddleware:
    """AuthMiddleware with PUBLIC default — every unknown user passes through."""
    return AuthMiddleware({}, TrustLevel.PUBLIC)


def _make_aiogram_msg(user_id: int = 42, is_bot: bool = False) -> SimpleNamespace:
    """Minimal aiogram Message stub."""
    return SimpleNamespace(
        chat=SimpleNamespace(id=123, type="private"),
        from_user=SimpleNamespace(
            id=user_id, full_name="Alice", is_bot=is_bot
        ),
        text="hello",
        date=datetime.now(timezone.utc),
        message_thread_id=None,
        message_id=99,
        entities=None,
    )


def _make_voice_msg(user_id: int = 42, is_bot: bool = False) -> SimpleNamespace:
    """Minimal aiogram voice Message stub."""
    voice = SimpleNamespace(file_id="voice123", duration=3, file_size=1024)
    return SimpleNamespace(
        chat=SimpleNamespace(id=123, type="private"),
        from_user=SimpleNamespace(id=user_id, full_name="Alice", is_bot=is_bot),
        voice=voice,
        audio=None,
        video_note=None,
        date=datetime.now(timezone.utc),
        message_thread_id=None,
        message_id=99,
    )


def _make_discord_msg(
    user_id: int = 99,
    is_bot: bool = False,
    bot_user: object = None,
) -> SimpleNamespace:
    """Minimal discord.py Message stub."""
    return SimpleNamespace(
        guild=SimpleNamespace(id=111),
        channel=SimpleNamespace(id=333, send=AsyncMock()),
        author=SimpleNamespace(
            id=user_id, name="Alice", display_name="Alice", bot=is_bot
        ),
        content="hello",
        created_at=datetime.now(timezone.utc),
        id=555,
        mentions=[],
    )


# ---------------------------------------------------------------------------
# SC-7: TelegramAdapter._on_message() does NOT call _normalize() when BLOCKED
# ---------------------------------------------------------------------------


class TestTelegramAdapterOnMessageAuthGate:
    @pytest.mark.asyncio
    async def test_blocked_user_skips_normalize(
        self, blocked_auth: AuthMiddleware
    ) -> None:
        """SC-7: BLOCKED user → _normalize() is never called."""
        from lyra.adapters.telegram import TelegramAdapter

        # Arrange
        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        hub.inbound_bus.put = MagicMock()
        adapter = TelegramAdapter(
            bot_id="main",
            token="test-token",
            hub=hub,
            auth=blocked_auth,  # type: ignore[call-arg]
        )
        aiogram_msg = _make_aiogram_msg(user_id=999)

        # Act
        with patch.object(adapter, "_normalize") as mock_normalize:
            await adapter._on_message(aiogram_msg)

        # Assert — _normalize must never be called for BLOCKED users
        mock_normalize.assert_not_called()

    @pytest.mark.asyncio
    async def test_blocked_user_message_never_reaches_bus(
        self, blocked_auth: AuthMiddleware
    ) -> None:
        """SC-7: BLOCKED user → hub.inbound_bus.put is never called."""
        from lyra.adapters.telegram import TelegramAdapter

        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        hub.inbound_bus.put = MagicMock()
        adapter = TelegramAdapter(
            bot_id="main",
            token="test-token",
            hub=hub,
            auth=blocked_auth,  # type: ignore[call-arg]
        )
        aiogram_msg = _make_aiogram_msg(user_id=999)

        await adapter._on_message(aiogram_msg)

        hub.inbound_bus.put.assert_not_called()

    @pytest.mark.asyncio
    async def test_allowed_user_calls_normalize(
        self, owner_auth: AuthMiddleware
    ) -> None:
        """Non-BLOCKED user → _normalize() IS called."""
        from lyra.adapters.telegram import TelegramAdapter

        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        hub.inbound_bus.put = MagicMock()
        adapter = TelegramAdapter(
            bot_id="main",
            token="test-token",
            hub=hub,
            auth=owner_auth,  # type: ignore[call-arg]
        )
        # user_id=1 is in owner_auth trust_map as OWNER
        aiogram_msg = _make_aiogram_msg(user_id=1)

        _ret = MagicMock()
        with (
            patch.object(adapter, "_normalize", return_value=_ret) as mock_normalize,
            patch.object(adapter, "_push_to_hub", new_callable=AsyncMock),
        ):
            await adapter._on_message(aiogram_msg)

        mock_normalize.assert_called_once()

    @pytest.mark.asyncio
    async def test_blocked_user_does_not_raise(
        self, blocked_auth: AuthMiddleware
    ) -> None:
        """BLOCKED early-return must not raise — aiogram expects HTTP 200."""
        from lyra.adapters.telegram import TelegramAdapter

        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        adapter = TelegramAdapter(
            bot_id="main",
            token="test-token",
            hub=hub,
            auth=blocked_auth,  # type: ignore[call-arg]
        )
        aiogram_msg = _make_aiogram_msg(user_id=999)

        # Must not raise
        await adapter._on_message(aiogram_msg)

    @pytest.mark.asyncio
    async def test_public_user_calls_normalize(
        self, public_auth: AuthMiddleware
    ) -> None:
        """PUBLIC default → unknown user passes through, _normalize() is called."""
        from lyra.adapters.telegram import TelegramAdapter

        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        hub.inbound_bus.put = MagicMock()
        adapter = TelegramAdapter(
            bot_id="main",
            token="test-token",
            hub=hub,
            auth=public_auth,  # type: ignore[call-arg]
        )
        # user_id=999 is not in the trust_map → falls to PUBLIC default
        aiogram_msg = _make_aiogram_msg(user_id=999)

        _ret = MagicMock()
        with (
            patch.object(adapter, "_normalize", return_value=_ret) as mock_normalize,
            patch.object(adapter, "_push_to_hub", new_callable=AsyncMock),
        ):
            await adapter._on_message(aiogram_msg)

        mock_normalize.assert_called_once()


# ---------------------------------------------------------------------------
# SC-8: TelegramAdapter._on_voice_message() does NOT call _normalize() when BLOCKED
# ---------------------------------------------------------------------------


class TestTelegramAdapterOnVoiceAuthGate:
    @pytest.mark.asyncio
    async def test_blocked_user_skips_voice_processing(
        self, blocked_auth: AuthMiddleware
    ) -> None:
        """SC-8: BLOCKED user → voice handler returns early, no audio download."""
        from lyra.adapters.telegram import TelegramAdapter

        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        hub.inbound_bus.put = MagicMock()
        adapter = TelegramAdapter(
            bot_id="main",
            token="test-token",
            hub=hub,
            auth=blocked_auth,  # type: ignore[call-arg]
        )
        bot_mock = AsyncMock()
        bot_mock.send_chat_action = AsyncMock()
        adapter.bot = bot_mock

        voice_msg = _make_voice_msg(user_id=999)

        _dl = patch.object(adapter, "_download_audio", new_callable=AsyncMock)
        with _dl as mock_dl:
            await adapter._on_voice_message(voice_msg)

        # Audio download must never happen for BLOCKED users
        mock_dl.assert_not_called()
        hub.inbound_bus.put.assert_not_called()

    @pytest.mark.asyncio
    async def test_blocked_voice_does_not_raise(
        self, blocked_auth: AuthMiddleware
    ) -> None:
        """BLOCKED early-return in voice handler must not raise."""
        from lyra.adapters.telegram import TelegramAdapter

        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        adapter = TelegramAdapter(
            bot_id="main",
            token="test-token",
            hub=hub,
            auth=blocked_auth,  # type: ignore[call-arg]
        )
        voice_msg = _make_voice_msg(user_id=999)

        # Must not raise
        await adapter._on_voice_message(voice_msg)

    @pytest.mark.asyncio
    async def test_allowed_user_voice_calls_download(
        self, owner_auth: AuthMiddleware
    ) -> None:
        """SC-8 positive path: allowed user → _download_audio is called."""
        from lyra.adapters.telegram import TelegramAdapter

        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        hub.inbound_bus.put = MagicMock()
        adapter = TelegramAdapter(
            bot_id="main",
            token="test-token",
            hub=hub,
            auth=owner_auth,  # type: ignore[call-arg]
        )
        bot_mock = AsyncMock()
        bot_mock.send_chat_action = AsyncMock()
        adapter.bot = bot_mock

        # user_id=1 maps to tg:user:1 which is OWNER in owner_auth fixture
        voice_msg = _make_voice_msg(user_id=1)

        with patch.object(
            adapter, "_download_audio", new_callable=AsyncMock
        ) as mock_dl:
            mock_dl.return_value = ("/tmp/test.ogg", 3.0)
            with patch.object(adapter, "_normalize", wraps=adapter._normalize):
                await adapter._on_voice_message(voice_msg)

        mock_dl.assert_called_once()


# ---------------------------------------------------------------------------
# SC-9: DiscordAdapter.on_message() does NOT call _normalize() when BLOCKED
# ---------------------------------------------------------------------------


class TestDiscordAdapterOnMessageAuthGate:
    @pytest.mark.asyncio
    async def test_blocked_user_skips_normalize(
        self, blocked_auth: AuthMiddleware
    ) -> None:
        """SC-9: BLOCKED Discord user → _normalize() is never called."""
        import discord

        from lyra.adapters.discord import DiscordAdapter

        # Arrange
        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        hub.inbound_bus.put = MagicMock()
        adapter = DiscordAdapter(
            hub=hub,
            bot_id="main",
            auth=blocked_auth,  # type: ignore[call-arg]
            intents=discord.Intents.none(),
        )
        bot_user = SimpleNamespace(id=999, bot=True)
        adapter._bot_user = bot_user

        discord_msg = _make_discord_msg(user_id=42, is_bot=False)

        # Act
        with patch.object(adapter, "_normalize") as mock_normalize:
            await adapter.on_message(discord_msg)

        # Assert
        mock_normalize.assert_not_called()

    @pytest.mark.asyncio
    async def test_blocked_user_message_never_reaches_bus(
        self, blocked_auth: AuthMiddleware
    ) -> None:
        """SC-9: BLOCKED Discord user → hub.inbound_bus.put never called."""
        import discord

        from lyra.adapters.discord import DiscordAdapter

        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        hub.inbound_bus.put = MagicMock()
        adapter = DiscordAdapter(
            hub=hub,
            bot_id="main",
            auth=blocked_auth,  # type: ignore[call-arg]
            intents=discord.Intents.none(),
        )
        bot_user = SimpleNamespace(id=999, bot=True)
        adapter._bot_user = bot_user

        discord_msg = _make_discord_msg(user_id=42)
        await adapter.on_message(discord_msg)

        hub.inbound_bus.put.assert_not_called()

    @pytest.mark.asyncio
    async def test_allowed_user_calls_normalize(
        self, discord_owner_auth: AuthMiddleware
    ) -> None:
        """Non-BLOCKED Discord user → _normalize() IS called."""
        import discord

        from lyra.adapters.discord import DiscordAdapter

        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        hub.inbound_bus.put = MagicMock()
        adapter = DiscordAdapter(
            hub=hub,
            bot_id="main",
            auth=discord_owner_auth,  # type: ignore[call-arg]
            intents=discord.Intents.none(),
        )
        bot_user = SimpleNamespace(id=999, bot=True)
        adapter._bot_user = bot_user

        # user_id=42 is in discord_owner_auth as OWNER
        discord_msg = _make_discord_msg(user_id=42)

        _ret = MagicMock()
        _norm = patch.object(adapter, "_normalize", return_value=_ret)
        _bus = patch.object(adapter._hub.inbound_bus, "put", MagicMock())
        with _norm as mock_normalize, _bus:
            await adapter.on_message(discord_msg)

        mock_normalize.assert_called_once()

    @pytest.mark.asyncio
    async def test_public_user_calls_normalize(
        self, public_auth: AuthMiddleware
    ) -> None:
        """PUBLIC default → unknown Discord user passes through."""
        import discord

        from lyra.adapters.discord import DiscordAdapter

        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        hub.inbound_bus.put = MagicMock()
        adapter = DiscordAdapter(
            hub=hub,
            bot_id="main",
            auth=public_auth,  # type: ignore[call-arg]
            intents=discord.Intents.none(),
        )
        bot_user = SimpleNamespace(id=999, bot=True)
        adapter._bot_user = bot_user

        # user_id=77 is not in the trust_map → falls to PUBLIC default
        discord_msg = _make_discord_msg(user_id=77)

        _ret = MagicMock()
        _norm = patch.object(adapter, "_normalize", return_value=_ret)
        _bus = patch.object(adapter._hub.inbound_bus, "put", MagicMock())
        with _norm as mock_normalize, _bus:
            await adapter.on_message(discord_msg)

        mock_normalize.assert_called_once()


# ---------------------------------------------------------------------------
# SC-10: Rejection log contains user_id, channel, timestamp for BLOCKED messages
# ---------------------------------------------------------------------------


class TestAuthRejectionLogging:
    @pytest.mark.asyncio
    async def test_telegram_blocked_logs_user_id_channel_timestamp(
        self, blocked_auth: AuthMiddleware, caplog: pytest.LogCaptureFixture
    ) -> None:
        """SC-10: BLOCKED Telegram message is logged with user_id, channel, ts."""
        from lyra.adapters.telegram import TelegramAdapter

        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        adapter = TelegramAdapter(
            bot_id="main",
            token="test-token",
            hub=hub,
            auth=blocked_auth,  # type: ignore[call-arg]
        )
        # user_id=999 is distinct from chat.id=123 to avoid false-positive matches
        aiogram_msg = _make_aiogram_msg(user_id=999)

        with caplog.at_level(logging.INFO, logger="lyra.adapters.telegram"):
            await adapter._on_message(aiogram_msg)

        # SC-10: log must contain user_id, channel, and ISO timestamp
        combined = " ".join(caplog.messages)
        assert "tg:user:999" in combined
        assert "telegram" in combined.lower()
        assert re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", combined)

    @pytest.mark.asyncio
    async def test_discord_blocked_logs_user_id_channel_timestamp(
        self, blocked_auth: AuthMiddleware, caplog: pytest.LogCaptureFixture
    ) -> None:
        """SC-10: BLOCKED Discord message is logged with user_id, channel, ts."""
        import discord

        from lyra.adapters.discord import DiscordAdapter

        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        adapter = DiscordAdapter(
            hub=hub,
            bot_id="main",
            auth=blocked_auth,  # type: ignore[call-arg]
            intents=discord.Intents.none(),
        )
        bot_user = SimpleNamespace(id=999, bot=True)
        adapter._bot_user = bot_user

        discord_msg = _make_discord_msg(user_id=42)

        with caplog.at_level(logging.INFO, logger="lyra.adapters.discord"):
            await adapter.on_message(discord_msg)

        # SC-10: log must contain user_id, channel, and ISO timestamp
        combined = " ".join(caplog.messages)
        assert "dc:user:42" in combined
        assert "discord" in combined.lower()
        assert re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", combined)


# ---------------------------------------------------------------------------
# SC-14: CLIAdapter.on_input() returns Message with trust_level=TrustLevel.OWNER
# ---------------------------------------------------------------------------


class TestCLIAdapterTrustLevel:
    def test_on_input_returns_owner_trust_level(self) -> None:
        """SC-14: CLIAdapter.on_input(text) returns Message with OWNER trust_level."""
        from lyra.adapters.cli import CLIAdapter

        # Arrange
        adapter = CLIAdapter()

        # Act
        msg = adapter.on_input("hello world")

        # Assert
        assert msg.trust_level == TrustLevel.OWNER

    def test_on_input_returns_message_with_text(self) -> None:
        """CLIAdapter.on_input preserves the text in the message content."""
        from lyra.adapters.cli import CLIAdapter

        adapter = CLIAdapter()
        msg = adapter.on_input("test input")

        from lyra.core.message import TextContent

        assert isinstance(msg.content, TextContent)
        assert msg.content.text == "test input"

    def test_on_input_trust_level_is_never_blocked(self) -> None:
        """CLI messages must never be BLOCKED regardless of input."""
        from lyra.adapters.cli import CLIAdapter

        adapter = CLIAdapter()
        for text in ["", "hello", "DROP TABLE users;", "   "]:
            msg = adapter.on_input(text)
            assert msg.trust_level != TrustLevel.BLOCKED

    def test_on_input_custom_user_id(self) -> None:
        """CLIAdapter.on_input with explicit user_id still gets OWNER trust."""
        from lyra.adapters.cli import CLIAdapter

        adapter = CLIAdapter()
        msg = adapter.on_input("hi", user_id="cli:user:custom")
        assert msg.trust_level == TrustLevel.OWNER
        assert msg.user_id == "cli:user:custom"
