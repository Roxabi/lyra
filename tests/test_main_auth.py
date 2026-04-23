"""Tests for __main__: agent factory and auth config validation (T4, T6)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import lyra.__main__ as main_mod
import lyra.bootstrap.factory.agent_factory as agent_factory_mod
import lyra.bootstrap.factory.unified as unified_mod
from lyra.core.agent import Agent
from lyra.core.agent.agent_config import ModelConfig
from tests.conftest import patch_auth_config_test

# ---------------------------------------------------------------------------
# T4 — Agent factory: _create_agent selects by backend
# ---------------------------------------------------------------------------


class TestAgentFactory:
    def test_cli_backend_creates_simple_agent(self) -> None:
        from lyra.agents.simple_agent import SimpleAgent

        config = Agent(
            name="test",
            system_prompt="",
            memory_namespace="test",
            llm_config=ModelConfig(backend="claude-cli"),
        )
        cli_pool = MagicMock()
        agent = agent_factory_mod._create_agent(config, cli_pool)
        assert isinstance(agent, SimpleAgent)

    def test_unknown_backend_raises(self) -> None:
        config = Agent(
            name="test",
            system_prompt="",
            memory_namespace="test",
            llm_config=ModelConfig(backend="unknown"),
        )
        with pytest.raises(ValueError, match="Unknown backend"):
            agent_factory_mod._create_agent(config, None)

    def test_cli_backend_without_pool_raises(self) -> None:
        config = Agent(
            name="test",
            system_prompt="",
            memory_namespace="test",
            llm_config=ModelConfig(backend="claude-cli"),
        )
        with pytest.raises(RuntimeError, match="CliPool required"):
            agent_factory_mod._create_agent(config, None)


# ---------------------------------------------------------------------------
# T6 — AuthMiddleware.from_config tests (inlined into _main)
# ---------------------------------------------------------------------------


class TestAuthConfig:
    async def test_missing_telegram_section_exits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No bot config at all causes SystemExit when _main() runs."""
        patch_auth_config_test(monkeypatch)
        monkeypatch.setattr(main_mod, "_load_raw_config", lambda: {})
        stop = asyncio.Event()
        stop.set()
        # With no config, _bootstrap_unified exits with "No adapters configured"
        # which includes "auth.telegram_bots" — matches pattern "auth.telegram".
        with pytest.raises(SystemExit, match="auth.telegram"):
            await main_mod._main(_stop=stop)

    async def test_discord_section_optional_when_telegram_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Missing discord auth is allowed when telegram is configured."""
        from lyra.core.agent.agent_models import AgentRow

        patch_auth_config_test(monkeypatch)
        # Use the multi-bot format (telegram_bots) since load_multibot_config
        # now routes through _bootstrap_unified even for legacy-style configs.
        monkeypatch.setattr(
            main_mod,
            "_load_raw_config",
            lambda: {
                "telegram": {"bots": [{"bot_id": "main"}]},
                "auth": {"telegram_bots": [{"bot_id": "main", "default": "public"}]},
            },
        )
        # Make the fake agent store return a row so agent_row_to_config is reached
        _fake_row = AgentRow(
            name="lyra_default",
            backend="claude-cli",
            model="claude-sonnet-4-5",
        )
        import lyra.bootstrap.bootstrap_stores as stores_mod_local

        _fake_agent_store = MagicMock()
        _fake_agent_store.connect = AsyncMock()
        _fake_agent_store.close = AsyncMock()
        _fake_agent_store.get_bot_agent = MagicMock(return_value=None)
        _fake_agent_store.get = MagicMock(return_value=_fake_row)
        _fake_agent_store.set_bot_agent = AsyncMock()
        monkeypatch.setattr(
            stores_mod_local, "AgentStore", lambda **kwargs: _fake_agent_store
        )
        # Sentinel: if we reach agent_row_to_config, auth validation passed.
        monkeypatch.setattr(
            unified_mod,
            "agent_row_to_config",
            lambda row, **kw: (_ for _ in ()).throw(SystemExit("past_auth")),
        )
        stop = asyncio.Event()
        stop.set()
        with pytest.raises(SystemExit, match="past_auth"):
            await main_mod._main(_stop=stop)

    async def test_invalid_default_exits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Invalid default in auth config causes SystemExit when _main() runs."""
        patch_auth_config_test(monkeypatch)
        # Use the multi-bot format (telegram_bots) since load_multibot_config
        # routes through _bootstrap_unified.
        monkeypatch.setattr(
            main_mod,
            "_load_raw_config",
            lambda: {
                "telegram": {"bots": [{"bot_id": "main"}]},
                "auth": {
                    "telegram_bots": [{"bot_id": "main", "default": "invalid_level"}],
                },
            },
        )
        stop = asyncio.Event()
        stop.set()
        with pytest.raises(SystemExit, match="invalid_level"):
            await main_mod._main(_stop=stop)
