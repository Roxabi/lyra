"""Tests for bootstrap MissingCredentialsError (issue #262, S2).

Verifies that the bootstrap path raises MissingCredentialsError when
CredentialStore.get_full() returns None, and that the error class exposes
the expected attributes and message content.

These tests operate at the unit level: they mock CredentialStore + other heavy
adapters so no real network or filesystem access is needed.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

import lyra.__main__ as main_mod
from lyra.core.auth import AuthMiddleware
from lyra.errors import MissingCredentialsError
from tests.conftest import make_fake_stores, patch_bootstrap_common

# ---------------------------------------------------------------------------
# TestMissingCredentials — bootstrap raises MissingCredentialsError
# ---------------------------------------------------------------------------


class TestMissingCredentials:
    """CredentialStore.get_full() returns None → MissingCredentialsError raised."""

    async def test_missing_telegram_credentials_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """telegram get_full() → None raises MissingCredentialsError."""
        # Arrange
        patch_bootstrap_common(monkeypatch)
        _, fake_cred_store = make_fake_stores(monkeypatch, tg_creds=None)

        monkeypatch.setattr(
            main_mod,
            "_load_raw_config",
            lambda: {
                "telegram": {"bots": [{"bot_id": "main"}]},
                "auth": {"telegram_bots": [{"bot_id": "main", "default": "public"}]},
            },
        )
        # Patch from_bot_config so auth validation passes
        monkeypatch.setattr(
            AuthMiddleware,
            "from_bot_config",
            classmethod(lambda cls, raw, platform, bot_id, **kw: MagicMock()),
        )

        stop = asyncio.Event()
        stop.set()

        # Act + Assert — MissingCredentialsError is caught and converted to SystemExit
        with pytest.raises(SystemExit) as exc_info:
            await main_mod._main(_stop=stop)

        assert "telegram" in str(exc_info.value.code)
        assert "main" in str(exc_info.value.code)

    async def test_missing_discord_credentials_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """discord get_full() → None raises MissingCredentialsError."""
        # Arrange
        patch_bootstrap_common(monkeypatch)
        _, fake_cred_store = make_fake_stores(
            monkeypatch, tg_creds=("tg-token", "tg-secret"), dc_creds=None
        )

        monkeypatch.setattr(
            main_mod,
            "_load_raw_config",
            lambda: {
                "discord": {"bots": [{"bot_id": "main"}]},
                "auth": {"discord_bots": [{"bot_id": "main", "default": "public"}]},
            },
        )
        monkeypatch.setattr(
            AuthMiddleware,
            "from_bot_config",
            classmethod(lambda cls, raw, platform, bot_id, **kw: MagicMock()),
        )

        stop = asyncio.Event()
        stop.set()

        # Act + Assert — MissingCredentialsError is caught and converted to SystemExit
        with pytest.raises(SystemExit) as exc_info:
            await main_mod._main(_stop=stop)

        assert "discord" in str(exc_info.value.code)
        assert "main" in str(exc_info.value.code)

    async def test_missing_credentials_error_message_contains_hint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """MissingCredentialsError message includes the CLI hint."""
        # Arrange — test the error class directly, no bootstrap wiring needed
        err = MissingCredentialsError("telegram", "my-bot")

        # Assert
        assert "telegram" in str(err)
        assert "my-bot" in str(err)
        assert "lyra bot add" in str(err)

    def test_missing_credentials_error_attributes(self) -> None:
        """MissingCredentialsError exposes .platform and .bot_id attributes."""
        # Arrange + Act
        err = MissingCredentialsError("discord", "secondary")

        # Assert
        assert err.platform == "discord"
        assert err.bot_id == "secondary"
