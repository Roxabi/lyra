"""Tests for bootstrap.auth_seeding — build_bot_auths."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from factory.bootstrap.auth_seeding import build_bot_auths
from factory.bootstrap.wiring.auth import BotAuthDeps
from factory.core.agent.bot_models import BotRow
from factory.infrastructure.stores.identity.auth_store import AuthStore
from factory.infrastructure.stores.identity.identity_alias_store import (
    IdentityAliasStore,
)
from tests.factories.stores import make_auth_store
from tests.helpers.bot_store import make_bot_store


class TestBootstrapNoIdentitySeed:
    async def test_hub_standalone_does_not_seed_identity(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Hub boot must not call any identity/grant seed helper."""
        monkeypatch.setenv("NATS_URL", "nats://localhost:4222")
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))

        seed_calls: list[str] = []

        def _trap_seed(*_a, **_kw):
            seed_calls.append("seed")
            raise RuntimeError("seed should not run")

        import factory.bootstrap.standalone.hub_standalone as hub_standalone_mod

        for name in (
            "seed_identity_and_grants",
            "seed_agent_grants_from_bots",
            "seed_auth_and_users",
            "seed_grants_from_bots",
        ):
            if hasattr(hub_standalone_mod, name):
                monkeypatch.setattr(hub_standalone_mod, name, _trap_seed)

        fake_nc = MagicMock()
        fake_nc.close = AsyncMock()
        monkeypatch.setattr(
            hub_standalone_mod, "nats_connect", AsyncMock(return_value=fake_nc)
        )
        monkeypatch.setattr(hub_standalone_mod, "acquire_lockfile", lambda: None)
        monkeypatch.setattr(hub_standalone_mod, "release_lockfile", lambda: None)

        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def fake_open_stores(vault_dir, nc):
            fake_stores = MagicMock()
            fake_stores.auth = MagicMock(spec=AuthStore)
            fake_stores.grant = MagicMock()
            fake_stores.bot = MagicMock()
            fake_stores.user = MagicMock()
            fake_stores.message_index = MagicMock()
            fake_stores.message_index.cleanup_older_than = AsyncMock(return_value=0)
            yield fake_stores

        monkeypatch.setattr(hub_standalone_mod, "open_stores", fake_open_stores)
        monkeypatch.setattr(
            hub_standalone_mod,
            "build_inbound_bus",
            lambda nc, raw_config: (MagicMock(), MagicMock()),
        )
        monkeypatch.setattr(
            hub_standalone_mod,
            "build_bot_auths",
            MagicMock(side_effect=RuntimeError("test-sentinel: abort after auth")),
        )

        from factory.bootstrap.standalone.hub_standalone import (
            _bootstrap_hub_standalone,
        )

        with pytest.raises(RuntimeError, match="test-sentinel"):
            await _bootstrap_hub_standalone({})

        assert seed_calls == []


class TestBuildBotAuthsRaisesWithoutAdapters:
    def test_build_bot_auths_raises_without_adapters(self) -> None:
        fake_auth_store = MagicMock(spec=AuthStore)
        fake_bot_store = MagicMock()
        fake_bot_store.get_all.return_value = []

        with pytest.raises(ValueError, match="No bots configured"):
            build_bot_auths({}, fake_auth_store, fake_bot_store)

    def test_build_bot_auths_error_message_contains_lyra_bot_init(self) -> None:
        fake_auth_store = MagicMock(spec=AuthStore)
        fake_bot_store = MagicMock()
        fake_bot_store.get_all.return_value = []

        with pytest.raises(ValueError) as exc_info:
            build_bot_auths({}, fake_auth_store, fake_bot_store)

        assert "lyra bot init" in str(exc_info.value)


class TestRosterFromStore:
    async def test_roster_from_store(self, tmp_path: Path) -> None:
        bot_store = await make_bot_store(tmp_path)
        auth_store = await make_auth_store(tmp_path)

        try:
            seeded_row = BotRow(
                platform="telegram",
                bot_id="seeded_tg",
                agent="lyra_default",
            )
            await bot_store.upsert(seeded_row)

            raw_config: dict = {}

            _circuit_registry, _admin_ids, tg_bot_auths, _dc_bot_auths = (
                build_bot_auths(raw_config, auth_store, bot_store)
            )

            assert len(tg_bot_auths) >= 1
            bot_cfg, _auth = tg_bot_auths[0]
            assert bot_cfg.bot_id == "seeded_tg"
        finally:
            await bot_store.close()
            await auth_store.close()


class TestAliasStoreParity:
    def test_build_bot_auths_threads_alias_store_into_deps(self) -> None:
        fake_auth_store = MagicMock(spec=AuthStore)
        fake_bot_store = MagicMock()
        fake_bot_store.get_all.return_value = []
        fake_alias_store = MagicMock(spec=IdentityAliasStore)

        captured_deps: list[BotAuthDeps] = []

        def fake_build_bot_auths(deps: BotAuthDeps):
            captured_deps.append(deps)
            return [], []

        with (
            patch(
                "factory.bootstrap.auth_seeding._build_bot_auths",
                side_effect=fake_build_bot_auths,
            ),
            patch(
                "factory.bootstrap.auth_seeding._load_circuit_config"
            ) as mock_circuit,
            patch(
                "factory.bootstrap.auth_seeding.multibot_config_from_store"
            ) as mock_multi,
        ):
            mock_circuit.return_value = (MagicMock(), frozenset())
            mock_multi.return_value = (MagicMock(bots=[]), MagicMock(bots=[]))

            with pytest.raises(ValueError, match="No bots configured"):
                build_bot_auths(
                    {},
                    fake_auth_store,
                    fake_bot_store,
                    fake_alias_store,
                )

        assert len(captured_deps) == 1
        assert captured_deps[0].alias_store is fake_alias_store

    def test_build_bot_auths_alias_store_defaults_to_none(self) -> None:
        fake_auth_store = MagicMock(spec=AuthStore)
        fake_bot_store = MagicMock()
        fake_bot_store.get_all.return_value = []

        captured_deps: list[BotAuthDeps] = []

        def fake_build_bot_auths(deps: BotAuthDeps):
            captured_deps.append(deps)
            return [], []

        with (
            patch(
                "factory.bootstrap.auth_seeding._build_bot_auths",
                side_effect=fake_build_bot_auths,
            ),
            patch(
                "factory.bootstrap.auth_seeding._load_circuit_config"
            ) as mock_circuit,
            patch(
                "factory.bootstrap.auth_seeding.multibot_config_from_store"
            ) as mock_multi,
        ):
            mock_circuit.return_value = (MagicMock(), frozenset())
            mock_multi.return_value = (MagicMock(bots=[]), MagicMock(bots=[]))

            with pytest.raises(ValueError, match="No bots configured"):
                build_bot_auths({}, fake_auth_store, fake_bot_store)

        assert len(captured_deps) == 1
        assert captured_deps[0].alias_store is None