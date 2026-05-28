"""Tests for lyra.bootstrap.auth_seeding — seed_grants_from_bots and build_bot_auths."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.bootstrap.auth_seeding import build_bot_auths
from lyra.infrastructure.stores.auth_store import AuthStore

# ---------------------------------------------------------------------------
# test_bootstrap_calls_seed_grants_from_bots
# ---------------------------------------------------------------------------


class TestBootstrapCallsSeedGrantsFromBots:
    async def test_bootstrap_calls_seed_grants_from_bots(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_bootstrap_hub_standalone calls seed_grants_from_bots once.

        Drives the bootstrap past the NATS_URL guard with a mock NATS connection,
        then short-circuits just after seed_grants_from_bots so no real DB is needed.
        """
        # Arrange
        monkeypatch.setenv("NATS_URL", "nats://localhost:4222")
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))

        # Track the seed_grants_from_bots call
        seed_calls: list[tuple] = []

        async def fake_seed(auth_store, bot_store):
            seed_calls.append((auth_store, bot_store))
            # Raise to abort further bootstrap — we only need to verify the call
            raise RuntimeError("test-sentinel: abort after seed")

        import lyra.bootstrap.standalone.hub_standalone as hub_standalone_mod

        monkeypatch.setattr(hub_standalone_mod, "seed_grants_from_bots", fake_seed)

        # Patch NATS connection so we never touch a real server
        fake_nc = AsyncMock()
        fake_nc.close = AsyncMock()
        monkeypatch.setattr(
            hub_standalone_mod, "nats_connect", AsyncMock(return_value=fake_nc)
        )
        monkeypatch.setattr(hub_standalone_mod, "acquire_lockfile", lambda: None)
        monkeypatch.setattr(hub_standalone_mod, "release_lockfile", lambda: None)

        # Patch open_stores so the async context manager yields a fake stores object
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def fake_open_stores(vault_dir):
            fake_stores = MagicMock()
            fake_stores.auth = MagicMock(spec=AuthStore)
            fake_stores.bot = MagicMock()
            fake_stores.message_index = MagicMock()
            fake_stores.message_index.cleanup_older_than = AsyncMock(return_value=0)
            yield fake_stores

        monkeypatch.setattr(hub_standalone_mod, "open_stores", fake_open_stores)

        # Patch build_inbound_bus to avoid NATS bus construction
        fake_bus = MagicMock()
        fake_bus_cfg = MagicMock()
        monkeypatch.setattr(
            hub_standalone_mod,
            "build_inbound_bus",
            lambda nc, raw_config: (fake_bus, fake_bus_cfg),
        )

        raw_config = {
            "defaults": {"cwd": "/tmp"},
            "auth": {"telegram_bots": [], "discord_bots": []},
            "telegram": {"bots": [{"bot_id": "test_bot", "agent": "test_agent"}]},
            "discord": {"bots": []},
        }

        from lyra.bootstrap.standalone.hub_standalone import _bootstrap_hub_standalone

        # Act — expect the sentinel to bubble up
        with pytest.raises(RuntimeError, match="test-sentinel"):
            await _bootstrap_hub_standalone(raw_config)

        # Assert — seed_grants_from_bots was called once with auth+bot stores
        assert len(seed_calls) == 1, "seed_grants_from_bots must be called exactly once"
        passed_store, passed_bot = seed_calls[0]
        assert passed_store is not None
        assert passed_bot is not None


# ---------------------------------------------------------------------------
# test_build_bot_auths_raises_without_adapters
# ---------------------------------------------------------------------------


class TestBuildBotAuthsRaisesWithoutAdapters:
    def test_build_bot_auths_raises_without_adapters(self) -> None:
        """build_bot_auths raises ValueError when no telegram AND no discord bots."""
        # Arrange
        raw_config: dict = {
            "telegram": {"bots": []},
            "discord": {"bots": []},
            "auth": {"telegram_bots": [], "discord_bots": []},
        }
        fake_auth_store = MagicMock(spec=AuthStore)
        fake_bot_store = MagicMock()

        # Act / Assert
        with pytest.raises(ValueError, match="No adapters configured"):
            build_bot_auths(raw_config, fake_auth_store, fake_bot_store)
