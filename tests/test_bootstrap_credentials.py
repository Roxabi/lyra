"""Tests for bootstrap credential resolution (issue #262, S2).

Verifies that the bootstrap path raises MissingCredentialsError when
CredentialStore.get_full() returns None, and passes the token to the adapter
when credentials are present.

These tests operate at the unit level: they mock CredentialStore + other heavy
adapters so no real network or filesystem access is needed.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import lyra.__main__ as main_mod
from lyra.core.agent import Agent, ModelConfig
from lyra.core.auth import AuthMiddleware
from lyra.core.hub import Hub
from lyra.errors import MissingCredentialsError

# ---------------------------------------------------------------------------
# Shared helpers / fake adapters
# ---------------------------------------------------------------------------


class _FakeDp:
    async def start_polling(self, bot: object, **kwargs: object) -> None:
        await asyncio.sleep(1_000)


class _FakeTgAdapter:
    dp = _FakeDp()
    bot = MagicMock()
    _bot_id = "main"

    def __init__(self, **kwargs: object) -> None:
        pass

    async def send(self, msg: object, response: object) -> None:
        pass


class _FakeDcAdapter:
    def __init__(self, hub: Hub, **kwargs: object) -> None:
        pass

    async def start(self, token: str) -> None:
        await asyncio.sleep(1_000)

    async def close(self) -> None:
        pass

    async def send(self, msg: object, response: object) -> None:
        pass


def _make_fake_stores(
    monkeypatch: pytest.MonkeyPatch,
    *,
    tg_creds: tuple[str, str | None] | None = ("fake-token", "fake-secret"),
    dc_creds: tuple[str, str | None] | None = ("fake-dc-token", None),
) -> tuple[MagicMock, MagicMock]:
    """Patch LyraKeyring and CredentialStore in main_mod.

    Returns (fake_keyring, fake_cred_store) for assertions.
    """
    fake_keyring = MagicMock()
    fake_keyring.key = b"fake-key-32-bytes-for-fernet-key"

    fake_cred_store = MagicMock()
    fake_cred_store.connect = AsyncMock()
    fake_cred_store.close = AsyncMock()

    # get_full side_effect: return tg_creds for telegram, dc_creds for discord
    async def _get_full(platform: str, bot_id: str) -> tuple[str, str | None] | None:
        if platform == "telegram":
            return tg_creds
        if platform == "discord":
            return dc_creds
        return None

    fake_cred_store.get_full = AsyncMock(side_effect=_get_full)

    monkeypatch.setattr(
        main_mod,
        "LyraKeyring",
        MagicMock(load_or_create=AsyncMock(return_value=fake_keyring)),
    )
    monkeypatch.setattr(main_mod, "CredentialStore", lambda **kwargs: fake_cred_store)
    return fake_keyring, fake_cred_store


def _patch_common(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Patch shared dependencies (auth_store, load_dotenv, load_agent_config, adapters).

    Returns fake_auth_store so callers can assert on it.
    """
    monkeypatch.setattr(main_mod, "load_dotenv", lambda: None)

    fake_auth_store = MagicMock()
    fake_auth_store.connect = AsyncMock()
    fake_auth_store.seed_from_config = AsyncMock()
    fake_auth_store.close = AsyncMock()
    monkeypatch.setattr(main_mod, "AuthStore", lambda **kwargs: fake_auth_store)

    monkeypatch.setattr(
        main_mod,
        "load_agent_config",
        lambda name, **kw: Agent(
            name=name,
            system_prompt="test",
            memory_namespace="test",
            model_config=ModelConfig(backend="claude-cli"),
        ),
    )
    monkeypatch.setattr(
        main_mod, "TelegramAdapter", lambda **kwargs: _FakeTgAdapter()
    )
    monkeypatch.setattr(
        main_mod,
        "DiscordAdapter",
        lambda **kwargs: _FakeDcAdapter(hub=MagicMock()),
    )

    mock_tg_auth = MagicMock()
    mock_dc_auth = MagicMock()
    _auth_results = iter([mock_tg_auth, mock_dc_auth])
    monkeypatch.setattr(
        AuthMiddleware,
        "from_config",
        classmethod(lambda cls, raw, section, store=None: next(_auth_results)),
    )

    return fake_auth_store


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
        _patch_common(monkeypatch)
        _, fake_cred_store = _make_fake_stores(monkeypatch, tg_creds=None)

        monkeypatch.setattr(
            main_mod,
            "_load_raw_config",
            lambda: {
                "telegram": {"bots": [{"bot_id": "main"}]},
                "auth": {
                    "telegram_bots": [{"bot_id": "main", "default": "public"}]
                },
            },
        )
        # Patch from_bot_config so auth validation passes
        monkeypatch.setattr(
            AuthMiddleware,
            "from_bot_config",
            classmethod(lambda cls, raw, platform, bot_id, store=None: MagicMock()),
        )

        stop = asyncio.Event()
        stop.set()

        # Act + Assert
        with pytest.raises(MissingCredentialsError) as exc_info:
            await main_mod._main(_stop=stop)

        assert exc_info.value.platform == "telegram"
        assert exc_info.value.bot_id == "main"

    async def test_missing_discord_credentials_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """discord get_full() → None raises MissingCredentialsError."""
        # Arrange
        _patch_common(monkeypatch)
        _, fake_cred_store = _make_fake_stores(
            monkeypatch, tg_creds=("tg-token", "tg-secret"), dc_creds=None
        )

        monkeypatch.setattr(
            main_mod,
            "_load_raw_config",
            lambda: {
                "discord": {"bots": [{"bot_id": "main"}]},
                "auth": {
                    "discord_bots": [{"bot_id": "main", "default": "public"}]
                },
            },
        )
        monkeypatch.setattr(
            AuthMiddleware,
            "from_bot_config",
            classmethod(lambda cls, raw, platform, bot_id, store=None: MagicMock()),
        )

        stop = asyncio.Event()
        stop.set()

        # Act + Assert
        with pytest.raises(MissingCredentialsError) as exc_info:
            await main_mod._main(_stop=stop)

        assert exc_info.value.platform == "discord"
        assert exc_info.value.bot_id == "main"

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
        _patch_common(monkeypatch)
        expected_token = "real-telegram-token-xyz"
        _, fake_cred_store = _make_fake_stores(
            monkeypatch,
            tg_creds=(expected_token, "webhook-secret"),
            dc_creds=None,
        )

        captured_kwargs: list[dict] = []

        class CapturingTgAdapter(_FakeTgAdapter):
            def __init__(self, **kwargs: object) -> None:
                captured_kwargs.append(dict(kwargs))

        monkeypatch.setattr(main_mod, "TelegramAdapter", CapturingTgAdapter)
        monkeypatch.setattr(
            main_mod,
            "_load_raw_config",
            lambda: {
                "telegram": {"bots": [{"bot_id": "main"}]},
                "auth": {
                    "telegram_bots": [{"bot_id": "main", "default": "public"}]
                },
            },
        )
        monkeypatch.setattr(
            AuthMiddleware,
            "from_bot_config",
            classmethod(lambda cls, raw, platform, bot_id, store=None: MagicMock()),
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
        _patch_common(monkeypatch)
        expected_token = "real-discord-token-abc"
        _, fake_cred_store = _make_fake_stores(
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
            lambda hub, **kwargs: CapturingDcAdapter(hub=hub),
        )
        monkeypatch.setattr(
            main_mod,
            "_load_raw_config",
            lambda: {
                "discord": {"bots": [{"bot_id": "main"}]},
                "auth": {
                    "discord_bots": [{"bot_id": "main", "default": "public"}]
                },
            },
        )
        monkeypatch.setattr(
            AuthMiddleware,
            "from_bot_config",
            classmethod(lambda cls, raw, platform, bot_id, store=None: MagicMock()),
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
        _patch_common(monkeypatch)
        _, fake_cred_store = _make_fake_stores(
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
            classmethod(lambda cls, raw, platform, bot_id, store=None: MagicMock()),
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
        _patch_common(monkeypatch)
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
            main_mod,
            "LyraKeyring",
            MagicMock(load_or_create=AsyncMock(return_value=fake_keyring)),
        )
        monkeypatch.setattr(
            main_mod, "CredentialStore", lambda **kwargs: fake_cred_store
        )

        monkeypatch.setattr(
            main_mod,
            "_load_raw_config",
            lambda: {
                "telegram": {"bots": [{"bot_id": "main"}]},
                "auth": {
                    "telegram_bots": [{"bot_id": "main", "default": "public"}]
                },
            },
        )
        monkeypatch.setattr(
            AuthMiddleware,
            "from_bot_config",
            classmethod(lambda cls, raw, platform, bot_id, store=None: MagicMock()),
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
