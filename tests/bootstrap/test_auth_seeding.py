"""Tests for lyra.bootstrap.auth_seeding — seed_grants_from_bots and build_bot_auths."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lyra.bootstrap.auth_seeding import build_bot_auths
from lyra.bootstrap.wiring.auth import BotAuthDeps
from lyra.core.agent.bot_models import BotRow
from lyra.infrastructure.stores.auth_store import AuthStore
from lyra.infrastructure.stores.identity_alias_store import IdentityAliasStore
from tests.factories.stores import make_auth_store
from tests.helpers.bot_store import make_bot_store

# ---------------------------------------------------------------------------
# test_bootstrap_calls_seed_grants_from_bots
# ---------------------------------------------------------------------------


class TestBootstrapCallsSeedGrantsFromBots:
    async def test_bootstrap_calls_seed_grants_from_bots(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_bootstrap_hub_standalone calls seed_grants_from_bots once.

        Uses auth+bot stores. Drives the bootstrap past the NATS_URL guard
        with a mock NATS connection, then short-circuits just after
        seed_grants_from_bots so no real DB is needed.
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
        """build_bot_auths raises ValueError when BotStore is empty (no roster).

        Old contract raised "No adapters configured" from TOML parsing.
        New contract: the roster comes from bot_store.get_all(); an empty
        store raises ValueError with the new migration-hint message.
        """
        # Arrange — empty BotStore (roster is store-sourced, not TOML)
        raw_config: dict = {}
        fake_auth_store = MagicMock(spec=AuthStore)
        fake_bot_store = MagicMock()
        fake_bot_store.get_all.return_value = []  # empty roster

        # Act / Assert — new message guides operator to run 'lyra bot init'
        with pytest.raises(ValueError, match="No bots configured"):
            build_bot_auths(raw_config, fake_auth_store, fake_bot_store)

    def test_build_bot_auths_error_message_contains_lyra_bot_init(self) -> None:
        """SC#3 — the ValueError message contains 'lyra bot init' so operators know
        how to recover from an empty roster.

        Negative gate: deleting the guard in build_bot_auths would suppress the
        raise entirely, causing this test to fail (no exception raised at all).
        """
        # Arrange
        fake_auth_store = MagicMock(spec=AuthStore)
        fake_bot_store = MagicMock()
        fake_bot_store.get_all.return_value = []  # empty roster

        # Act / Assert
        with pytest.raises(ValueError) as exc_info:
            build_bot_auths({}, fake_auth_store, fake_bot_store)

        assert "lyra bot init" in str(exc_info.value), (
            f"Expected 'lyra bot init' in error message, got: {exc_info.value!r}"
        )


# ---------------------------------------------------------------------------
# test_roster_from_store
# ---------------------------------------------------------------------------


class TestRosterFromStore:
    async def test_roster_from_store(self, tmp_path: Path) -> None:
        """build_bot_auths reads the bot roster from BotStore, not from TOML.

        Verifies that a bot seeded into BotStore appears in tg_bot_auths even
        when the raw_config contains no [[telegram.bots]] section.  The roster
        is sourced exclusively from bot_store.get_all().
        """
        # Arrange — real stores backed by a tmp SQLite DB
        bot_store = await make_bot_store(tmp_path)
        auth_store = await make_auth_store(tmp_path)

        try:
            # Seed exactly one Telegram bot into BotStore (not in TOML)
            seeded_row = BotRow(
                platform="telegram",
                bot_id="seeded_tg",
                agent="lyra_default",
                default_trust="public",
            )
            await bot_store.upsert(seeded_row)

            # raw_config has NO [[telegram.bots]] and NO [[discord.bots]]
            # — TOML roster empty
            raw_config: dict = {}

            # Act — post-T3 this must succeed and return the seeded bot
            _circuit_registry, _admin_ids, tg_bot_auths, _dc_bot_auths = (
                build_bot_auths(raw_config, auth_store, bot_store)
            )

            # Assert — seeded_tg must appear in the telegram bot-auth list
            assert len(tg_bot_auths) >= 1, (
                "Expected at least one telegram bot-auth from BotStore, got none. "
                "build_bot_auths still reads roster from TOML instead of bot_store."
            )
            bot_cfg, _auth = tg_bot_auths[0]
            assert bot_cfg.bot_id == "seeded_tg", (
                f"Expected bot_id='seeded_tg', got {bot_cfg.bot_id!r}. "
                "Roster is not sourced from BotStore."
            )
        finally:
            await bot_store.close()
            await auth_store.close()


# ---------------------------------------------------------------------------
# test_alias_store_parity — hub-path threads alias_store into BotAuthDeps
# ---------------------------------------------------------------------------


class TestAliasStoreParity:
    def test_build_bot_auths_threads_alias_store_into_deps(self) -> None:
        """build_bot_auths passes alias_store into BotAuthDeps when provided.

        Parity gate: the hub-standalone path must forward its alias_store arg
        into the BotAuthDeps so Authenticator.from_bot_store receives it.
        Without the fix, alias_store defaults to None and identity-alias
        resolution is silently skipped for all bots in hub-standalone mode.

        This test fails if build_bot_auths ignores the alias_store parameter
        (i.e., still constructs BotAuthDeps without forwarding it).
        """
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
                "lyra.bootstrap.auth_seeding._build_bot_auths",
                side_effect=fake_build_bot_auths,
            ),
            patch("lyra.bootstrap.auth_seeding._load_circuit_config") as mock_circuit,
            patch(
                "lyra.bootstrap.auth_seeding.multibot_config_from_store"
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

        assert len(captured_deps) == 1, "Expected _build_bot_auths to be called once"
        assert captured_deps[0].alias_store is fake_alias_store, (
            "build_bot_auths did not forward alias_store into BotAuthDeps. "
            "Hub-standalone path will silently skip identity-alias resolution."
        )

    def test_build_bot_auths_alias_store_defaults_to_none(self) -> None:
        """build_bot_auths preserves None default when alias_store is omitted.

        Ensures backward-compatibility for callers that do not pass alias_store.
        """
        fake_auth_store = MagicMock(spec=AuthStore)
        fake_bot_store = MagicMock()
        fake_bot_store.get_all.return_value = []

        captured_deps: list[BotAuthDeps] = []

        def fake_build_bot_auths(deps: BotAuthDeps):
            captured_deps.append(deps)
            return [], []

        with (
            patch(
                "lyra.bootstrap.auth_seeding._build_bot_auths",
                side_effect=fake_build_bot_auths,
            ),
            patch("lyra.bootstrap.auth_seeding._load_circuit_config") as mock_circuit,
            patch(
                "lyra.bootstrap.auth_seeding.multibot_config_from_store"
            ) as mock_multi,
        ):
            mock_circuit.return_value = (MagicMock(), frozenset())
            mock_multi.return_value = (MagicMock(bots=[]), MagicMock(bots=[]))

            with pytest.raises(ValueError, match="No bots configured"):
                build_bot_auths({}, fake_auth_store, fake_bot_store)

        assert len(captured_deps) == 1
        assert captured_deps[0].alias_store is None
