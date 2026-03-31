"""Tests for bootstrap credential resolution (issue #262, S2).

Verifies that CredentialStore.get_full() is called correctly and that the
resolved token is forwarded to the platform adapter (Telegram / Discord).

These tests operate at the unit level: they mock CredentialStore + other heavy
adapters so no real network or filesystem access is needed.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import lyra.__main__ as main_mod
import lyra.bootstrap.multibot_stores as stores_mod
import lyra.bootstrap.multibot_wiring as wiring_mod
from lyra.core.auth import AuthMiddleware
from tests.conftest import (
    _FakeDcAdapter,
    _FakeTgAdapter,
    make_fake_stores,
    patch_bootstrap_common,
)

# ---------------------------------------------------------------------------
# TestCredentialResolution — credentials resolved and passed to adapter
# ---------------------------------------------------------------------------


class TestCredentialResolution:
    """CredentialStore.get_full() returns a token → it is passed to the adapter."""

    async def test_telegram_token_passed_to_adapter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """TelegramAdapter receives the token resolved from CredentialStore."""
        # Arrange
        patch_bootstrap_common(monkeypatch)
        expected_token = "real-telegram-token-xyz"
        _, _ = make_fake_stores(
            monkeypatch,
            tg_creds=(expected_token, "webhook-secret"),
            dc_creds=None,
        )

        # Use a random free port so the health server does not conflict
        # with other parallel tests or daemons that may hold port 8443.
        import socket as _socket

        with _socket.socket() as _s:
            _s.bind(("127.0.0.1", 0))
            free_port = _s.getsockname()[1]

        monkeypatch.setenv("LYRA_HEALTH_PORT", str(free_port))

        captured_kwargs: list[dict] = []

        class CapturingTgAdapter(_FakeTgAdapter):
            def __init__(self, **kwargs: object) -> None:
                super().__init__(**kwargs)
                captured_kwargs.append(dict(kwargs))

        monkeypatch.setattr(main_mod, "TelegramAdapter", CapturingTgAdapter)
        monkeypatch.setattr(wiring_mod, "TelegramAdapter", CapturingTgAdapter)
        monkeypatch.setattr(
            main_mod,
            "_load_raw_config",
            lambda: {
                "telegram": {"bots": [{"bot_id": "main"}]},
                "auth": {"telegram_bots": [{"bot_id": "main", "default": "public"}]},
            },
        )
        monkeypatch.setattr(
            AuthMiddleware,
            "from_bot_config",
            classmethod(lambda cls, raw, platform, bot_id, **kw: MagicMock()),
        )

        stop = asyncio.Event()
        stop.set()

        # Act
        await main_mod._main(_stop=stop)

        # Assert
        assert len(captured_kwargs) == 1
        assert captured_kwargs[0]["token"] == expected_token

    async def test_discord_token_passed_to_adapter_start(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DiscordAdapter.start() receives the token from CredentialStore.

        A CapturingDcAdapter records the token on start() so we can assert
        after the function returns.
        """
        # Arrange
        patch_bootstrap_common(monkeypatch)
        expected_token = "real-discord-token-abc"
        _, _ = make_fake_stores(
            monkeypatch,
            tg_creds=None,
            dc_creds=(expected_token, None),
        )

        # Use a random free port so the health server does not conflict with
        # other parallel tests that may hold port 8443.
        import socket as _socket

        with _socket.socket() as _s:
            _s.bind(("127.0.0.1", 0))
            free_port = _s.getsockname()[1]

        monkeypatch.setenv("LYRA_HEALTH_PORT", str(free_port))

        started_with: list[str] = []

        class CapturingDcAdapter(_FakeDcAdapter):
            async def start(self, token: str) -> None:
                started_with.append(token)
                # Yield control so the test doesn't hang waiting for this task
                await asyncio.sleep(0)

        monkeypatch.setattr(
            main_mod,
            "DiscordAdapter",
            lambda **kwargs: CapturingDcAdapter(),
        )
        monkeypatch.setattr(
            wiring_mod,
            "DiscordAdapter",
            lambda **kwargs: CapturingDcAdapter(),
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

        async def trigger() -> None:
            await asyncio.sleep(0.05)
            stop.set()

        trigger_task = asyncio.create_task(trigger())

        # Act
        await main_mod._main(_stop=stop)
        await trigger_task

        # Assert — start() was called with the correct token from CredentialStore
        assert expected_token in started_with

    async def test_cred_store_get_full_called_with_correct_args(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """get_full() is called with (platform, bot_id) from config."""
        # Arrange
        patch_bootstrap_common(monkeypatch)
        _, fake_cred_store = make_fake_stores(
            monkeypatch,
            tg_creds=("tok", "sec"),
            dc_creds=None,
        )

        monkeypatch.setattr(
            main_mod,
            "_load_raw_config",
            lambda: {
                "telegram": {"bots": [{"bot_id": "lyra-prod"}]},
                "auth": {
                    "telegram_bots": [{"bot_id": "lyra-prod", "default": "public"}]
                },
            },
        )
        monkeypatch.setattr(
            AuthMiddleware,
            "from_bot_config",
            classmethod(lambda cls, raw, platform, bot_id, **kw: MagicMock()),
        )

        stop = asyncio.Event()
        stop.set()

        # Act
        await main_mod._main(_stop=stop)

        # Assert — get_full must have been called with telegram + bot_id from config
        fake_cred_store.get_full.assert_any_await("telegram", "lyra-prod")

    async def test_cred_store_connected_before_get_full(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CredentialStore.connect() is called before get_full() during bootstrap."""
        # Arrange
        patch_bootstrap_common(monkeypatch)
        call_order: list[str] = []

        fake_keyring = MagicMock()
        fake_keyring.key = b"fake-key-32-bytes-for-fernet-key"

        fake_cred_store = MagicMock()

        async def _connect() -> None:
            call_order.append("connect")

        async def _get_full(
            platform: str, bot_id: str
        ) -> tuple[str, str | None] | None:
            call_order.append("get_full")
            return ("tok", None)

        fake_cred_store.connect = AsyncMock(side_effect=_connect)
        fake_cred_store.close = AsyncMock()
        fake_cred_store.get_full = AsyncMock(side_effect=_get_full)

        monkeypatch.setattr(
            stores_mod,
            "LyraKeyring",
            MagicMock(load_or_create=MagicMock(return_value=fake_keyring)),
        )
        monkeypatch.setattr(
            stores_mod, "CredentialStore", lambda **kwargs: fake_cred_store
        )

        monkeypatch.setattr(
            main_mod,
            "_load_raw_config",
            lambda: {
                "telegram": {"bots": [{"bot_id": "main"}]},
                "auth": {"telegram_bots": [{"bot_id": "main", "default": "public"}]},
            },
        )
        monkeypatch.setattr(
            AuthMiddleware,
            "from_bot_config",
            classmethod(lambda cls, raw, platform, bot_id, **kw: MagicMock()),
        )

        stop = asyncio.Event()
        stop.set()

        # Act
        await main_mod._main(_stop=stop)

        # Assert
        connect_idx = call_order.index("connect")
        get_full_idx = call_order.index("get_full")
        assert connect_idx < get_full_idx, (
            "CredentialStore.connect() must be called before get_full()"
        )
