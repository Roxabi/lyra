"""Tests for __main__: hub wiring, env var validation, graceful shutdown."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

import lyra.__main__ as main_mod
from lyra.core.agent import Agent, ModelConfig
from lyra.core.auth import AuthMiddleware
from lyra.core.hub import Hub
from lyra.core.message import Platform

# ---------------------------------------------------------------------------
# Fake adapters — minimal stand-ins that never touch the network
# ---------------------------------------------------------------------------


class _FakeDp:
    async def start_polling(self, bot: object, **kwargs: object) -> None:
        await asyncio.sleep(1_000)


class _FakeTgAdapter:
    dp = _FakeDp()
    bot = MagicMock()

    def __init__(self, **kwargs: object) -> None:
        pass

    async def send(self, msg: object, response: object) -> None:
        pass


class _FakeDcAdapter:
    def __init__(self, hub: Hub, **kwargs: object) -> None:
        self._hub = hub

    async def start(self, token: str) -> None:
        await asyncio.sleep(1_000)

    async def close(self) -> None:
        pass

    async def send(self, msg: object, response: object) -> None:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_auth_pair():
    """Return a (tg_auth, dc_auth) pair of MagicMocks."""
    return MagicMock(), MagicMock()


def _patch_all(monkeypatch: pytest.MonkeyPatch) -> list[Hub]:
    """Patch __main__ globals to avoid real network calls. Returns hub list."""
    captured: list[Hub] = []

    class CapturingDcAdapter(_FakeDcAdapter):
        def __init__(self, hub: Hub, **kwargs: object) -> None:
            captured.append(hub)
            super().__init__(hub, **kwargs)

    mock_tg_auth, mock_dc_auth = _make_mock_auth_pair()
    _auth_results = iter([mock_tg_auth, mock_dc_auth])
    monkeypatch.setattr(main_mod, "load_dotenv", lambda: None)
    monkeypatch.setattr(
        AuthMiddleware,
        "from_config",
        classmethod(lambda cls, raw, section, store=None: next(_auth_results)),
    )
    from unittest.mock import AsyncMock

    _fake_auth_store = MagicMock()
    _fake_auth_store.connect = AsyncMock()
    _fake_auth_store.seed_from_config = AsyncMock()
    _fake_auth_store.close = AsyncMock()
    monkeypatch.setattr(main_mod, "AuthStore", lambda **kwargs: _fake_auth_store)
    monkeypatch.setattr(
        main_mod,
        "load_telegram_config",
        lambda: MagicMock(token="t", webhook_secret="s", bot_username="b"),
    )
    monkeypatch.setattr(
        main_mod,
        "load_discord_config",
        lambda: MagicMock(token="d"),
    )
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
    monkeypatch.setattr(main_mod, "TelegramAdapter", lambda **kwargs: _FakeTgAdapter())
    monkeypatch.setattr(main_mod, "DiscordAdapter", CapturingDcAdapter)
    return captured


# ---------------------------------------------------------------------------
# T1 — Missing env vars exit immediately
# ---------------------------------------------------------------------------


class TestMissingEnvVars:
    def test_missing_telegram_token_exits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)

        from lyra.adapters.telegram import load_config

        with pytest.raises(SystemExit, match="TELEGRAM_TOKEN"):
            load_config()

    def test_missing_telegram_secret_exits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TELEGRAM_TOKEN", "fake")
        monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)

        from lyra.adapters.telegram import load_config

        with pytest.raises(SystemExit, match="TELEGRAM_WEBHOOK_SECRET"):
            load_config()

    def test_missing_discord_token_exits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DISCORD_TOKEN", raising=False)

        from lyra.adapters.discord import load_discord_config

        with pytest.raises(SystemExit, match="DISCORD_TOKEN"):
            load_discord_config()


# ---------------------------------------------------------------------------
# T2 — Hub wiring: both adapters + wildcard bindings registered
# ---------------------------------------------------------------------------


class TestHubWiring:
    async def test_registers_both_adapters(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = _patch_all(monkeypatch)
        stop = asyncio.Event()
        stop.set()

        await main_mod._main(_stop=stop)

        hub = captured[0]
        assert (Platform.TELEGRAM, "main") in hub.adapter_registry
        assert (Platform.DISCORD, "main") in hub.adapter_registry

    async def test_registers_wildcard_bindings(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = _patch_all(monkeypatch)
        stop = asyncio.Event()
        stop.set()

        await main_mod._main(_stop=stop)

        hub = captured[0]
        from lyra.core.hub import RoutingKey

        tg_binding = hub.bindings.get(RoutingKey(Platform.TELEGRAM, "main", "*"))
        dc_binding = hub.bindings.get(RoutingKey(Platform.DISCORD, "main", "*"))

        assert tg_binding is not None and tg_binding.agent_name == "lyra_default"
        assert dc_binding is not None and dc_binding.agent_name == "lyra_default"


# ---------------------------------------------------------------------------
# T3 — Graceful shutdown: tasks cancelled without CancelledError propagating
# ---------------------------------------------------------------------------


class TestGracefulShutdown:
    async def test_clean_exit_on_stop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_main() returns normally when stop fires — no exception raised."""
        _patch_all(monkeypatch)
        stop = asyncio.Event()
        stop.set()

        # Must not raise
        await main_mod._main(_stop=stop)

    async def test_delayed_stop_cancels_tasks(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A stop fired after a small delay also exits cleanly."""
        _patch_all(monkeypatch)
        stop = asyncio.Event()

        async def trigger() -> None:
            await asyncio.sleep(0.05)
            stop.set()

        trigger_task = asyncio.create_task(trigger())
        await main_mod._main(_stop=stop)
        await trigger_task  # ensure no lingering tasks


# ---------------------------------------------------------------------------
# T4 — Agent factory: _create_agent selects by backend
# ---------------------------------------------------------------------------


class TestAgentFactory:
    def test_cli_backend_creates_simple_agent(self) -> None:
        from lyra.agents.simple_agent import SimpleAgent
        from lyra.core.agent import Agent, ModelConfig

        config = Agent(
            name="test",
            system_prompt="",
            memory_namespace="test",
            model_config=ModelConfig(backend="claude-cli"),
        )
        cli_pool = MagicMock()
        agent = main_mod._create_agent(config, cli_pool)
        assert isinstance(agent, SimpleAgent)

    def test_sdk_backend_creates_anthropic_agent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
        from lyra.agents.anthropic_agent import AnthropicAgent
        from lyra.core.agent import Agent, ModelConfig

        config = Agent(
            name="test",
            system_prompt="",
            memory_namespace="test",
            model_config=ModelConfig(backend="anthropic-sdk"),
        )
        agent = main_mod._create_agent(config, None)
        assert isinstance(agent, AnthropicAgent)

    def test_unknown_backend_raises(self) -> None:
        from lyra.core.agent import Agent, ModelConfig

        config = Agent(
            name="test",
            system_prompt="",
            memory_namespace="test",
            model_config=ModelConfig(backend="unknown"),
        )
        with pytest.raises(ValueError, match="Unknown backend"):
            main_mod._create_agent(config, None)

    def test_cli_backend_without_pool_raises(self) -> None:
        from lyra.core.agent import Agent, ModelConfig

        config = Agent(
            name="test",
            system_prompt="",
            memory_namespace="test",
            model_config=ModelConfig(backend="claude-cli"),
        )
        with pytest.raises(RuntimeError, match="CliPool required"):
            main_mod._create_agent(config, None)


# ---------------------------------------------------------------------------
# T6 — AuthMiddleware.from_config tests (inlined into _main)
# ---------------------------------------------------------------------------


class TestAuthConfig:
    async def test_missing_telegram_section_exits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Missing [auth.telegram] causes SystemExit when _main() runs."""
        monkeypatch.setattr(main_mod, "load_dotenv", lambda: None)
        monkeypatch.setattr(
            main_mod,
            "load_telegram_config",
            lambda: MagicMock(token="t", webhook_secret="s", bot_username="b"),
        )
        monkeypatch.setattr(
            main_mod,
            "load_discord_config",
            lambda: MagicMock(token="d"),
        )
        monkeypatch.setattr(
            main_mod,
            "_load_raw_config",
            lambda: {},
        )
        stop = asyncio.Event()
        stop.set()
        with pytest.raises(SystemExit, match="auth.telegram"):
            await main_mod._main(_stop=stop)

    async def test_discord_section_optional_when_telegram_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Missing [auth.discord] is allowed when [auth.telegram] is configured."""
        monkeypatch.setattr(main_mod, "load_dotenv", lambda: None)
        monkeypatch.setattr(
            main_mod,
            "load_telegram_config",
            lambda: MagicMock(token="t", webhook_secret="s", bot_username="b"),
        )
        monkeypatch.setattr(
            main_mod,
            "load_discord_config",
            lambda: MagicMock(token="d"),
        )
        monkeypatch.setattr(
            main_mod,
            "_load_raw_config",
            lambda: {"auth": {"telegram": {"default": "public"}}},
        )
        # Sentinel: if we reach load_agent_config, auth validation passed.
        monkeypatch.setattr(
            main_mod,
            "load_agent_config",
            lambda name, **kw: (_ for _ in ()).throw(SystemExit("past_auth")),
        )
        stop = asyncio.Event()
        stop.set()
        with pytest.raises(SystemExit, match="past_auth"):
            await main_mod._main(_stop=stop)

    async def test_invalid_default_exits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Invalid default in [auth.telegram] causes SystemExit when _main() runs."""
        monkeypatch.setattr(main_mod, "load_dotenv", lambda: None)
        monkeypatch.setattr(
            main_mod,
            "load_telegram_config",
            lambda: MagicMock(token="t", webhook_secret="s", bot_username="b"),
        )
        monkeypatch.setattr(
            main_mod,
            "load_discord_config",
            lambda: MagicMock(token="d"),
        )
        monkeypatch.setattr(
            main_mod,
            "_load_raw_config",
            lambda: {
                "auth": {
                    "telegram": {"default": "invalid_level"},
                    "discord": {"default": "public"},
                }
            },
        )
        stop = asyncio.Event()
        stop.set()
        with pytest.raises(SystemExit, match="invalid_level"):
            await main_mod._main(_stop=stop)
