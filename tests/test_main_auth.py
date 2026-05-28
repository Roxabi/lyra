"""Tests for __main__: agent factory and auth config validation (T4, T6)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import lyra.__main__ as main_mod
import lyra.bootstrap.factory.agent_factory as agent_factory_mod
import lyra.bootstrap.factory.wiring_helpers as wiring_helpers_mod
from lyra.bootstrap.factory.agent_factory import CreateAgentDeps
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
        agent = agent_factory_mod._create_agent(
            CreateAgentDeps(config=config, cli_pool=cli_pool)
        )
        assert isinstance(agent, SimpleAgent)

    def test_unknown_backend_raises(self) -> None:
        # model_construct bypasses Pydantic validators — TEST-ONLY pattern.
        # Must NOT appear in production deserialization paths (NATS, DB rows),
        # which is exactly what ModelConfig._validate_backend was added to guard.
        # Here we use it to reach the factory's defense-in-depth check.
        llm_cfg = ModelConfig.model_construct(backend="unknown")
        config = Agent(
            name="test",
            system_prompt="",
            memory_namespace="test",
            llm_config=llm_cfg,
        )
        with pytest.raises(ValueError, match="Unknown backend"):
            agent_factory_mod._create_agent(
                CreateAgentDeps(config=config, cli_pool=None)
            )

    def test_cli_backend_without_pool_raises(self) -> None:
        config = Agent(
            name="test",
            system_prompt="",
            memory_namespace="test",
            llm_config=ModelConfig(backend="claude-cli"),
        )
        with pytest.raises(RuntimeError, match="CliPool required"):
            agent_factory_mod._create_agent(
                CreateAgentDeps(config=config, cli_pool=None)
            )


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
        # With no config, _bootstrap_unified exits with "No adapters configured".
        with pytest.raises(SystemExit, match="No adapters configured"):
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
            wiring_helpers_mod,
            "agent_row_to_config",
            lambda row, **kw: (_ for _ in ()).throw(SystemExit("past_auth")),
        )
        stop = asyncio.Event()
        stop.set()
        with pytest.raises(SystemExit, match="past_auth"):
            await main_mod._main(_stop=stop)

    async def test_invalid_default_exits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Invalid default_trust in BotStore causes SystemExit when _main() runs."""
        from unittest.mock import MagicMock

        import lyra.bootstrap.bootstrap_stores as stores_mod_local

        patch_auth_config_test(monkeypatch)
        _fake_bot_store = MagicMock()
        _fake_bot_store.connect = AsyncMock()
        _fake_bot_store.close = AsyncMock()
        _fake_bot_store.get = MagicMock(
            return_value=MagicMock(default_trust="invalid_level", trusted_roles=[])
        )
        monkeypatch.setattr(
            stores_mod_local, "BotStore", lambda **kwargs: _fake_bot_store
        )
        monkeypatch.setattr(
            main_mod,
            "_load_raw_config",
            lambda: {
                "telegram": {"bots": [{"bot_id": "main"}]},
            },
        )
        stop = asyncio.Event()
        stop.set()
        with pytest.raises(SystemExit, match="invalid_level"):
            await main_mod._main(_stop=stop)
