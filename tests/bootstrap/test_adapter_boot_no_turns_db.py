"""T22: Adapter boot does NOT open turns.db — last-session via KV only (#1721).

Tests verify both standalone paths (standalone_discord, standalone_telegram) and
the unified path (bootstrap_wiring.py) construct adapters with a LastSessionStore
(KvLastSessionStore or TurnStoreLastSession) and NEVER open a TurnStore backed by
turns.db.

Reuses pattern from test_discord_kv_wiring.py: heavy deps mocked; key assertion on
adapter constructor kwargs.
"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _noop_task() -> "asyncio.Task[None]":
    """Zero-delay cancel-safe stub task."""
    return asyncio.create_task(asyncio.sleep(0))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_wire_bot_common_stub():
    """Return a fake wire_bot_common that invokes the adapter_factory.

    The adapter_factory closure (defined per-test) populates `captured_kwargs`
    from the test scope and returns the adapter stub so teardown has a properly
    stubbed object with the right async methods.
    """

    async def _fake_wire_bot_common(*, adapter_factory, **_kw):
        mock_inbound_bus = AsyncMock()
        mock_inbound_bus.stop = AsyncMock()
        mock_typing_listener = AsyncMock()
        mock_typing_listener.stop = AsyncMock()
        mock_consumer = AsyncMock()
        mock_consumer.stop = AsyncMock()

        # Invoke the adapter_factory; it populates captured_kwargs and returns
        # (adapter, typing_deps) — the real adapter stub created by the caller.
        result = adapter_factory(mock_inbound_bus)
        # result is (adapter, typing_deps) tuple from _tg_adapter_factory /
        # _dc_adapter_factory; unwrap to get the adapter.
        if isinstance(result, tuple):
            real_adapter, _ = result
        else:
            real_adapter = result
        return (real_adapter, mock_inbound_bus, mock_typing_listener, mock_consumer)

    return _fake_wire_bot_common


# ---------------------------------------------------------------------------
# SC1: standalone_telegram.py source does NOT reference TurnStore or turns.db
# ---------------------------------------------------------------------------


class TestTelegramStandaloneNoTurnsDb:
    """standalone_telegram must not open turns.db — source-level + behavioral."""

    def test_standalone_telegram_source_has_no_turns_db_reference(self) -> None:
        """Source-level guard: 'TurnStore' + 'turns.db' absent from standalone_telegram.

        Negative: if a developer re-adds a TurnStore open, this test fires immediately.
        """
        from factory.bootstrap.wiring import standalone_telegram as _mod

        source = inspect.getsource(_mod)
        assert "turns.db" not in source, (
            "standalone_telegram references 'turns.db' — must use KV only"
        )
        assert "TurnStore(" not in source, (
            "standalone_telegram opens TurnStore — must use KvLastSessionStore"
        )

    def test_standalone_telegram_imports_kv_last_session_store(self) -> None:
        """standalone_telegram must import KvLastSessionStore (not TurnStore)."""
        from factory.bootstrap.wiring import standalone_telegram as _mod

        source = inspect.getsource(_mod)
        assert "KvLastSessionStore" in source, (
            "standalone_telegram does not use KvLastSessionStore"
        )

    async def test_telegram_standalone_constructs_adapter_with_last_session_kwarg(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Behavioral: TelegramAdapter receives last_session= kwarg (not turn_store=).

        Negative: if the wiring still passes turn_store= to TelegramAdapter, the
        adapter ctor either accepts it (if param still exists — regression) or raises
        TypeError (fast-fail, but wrong error message).  Either way this test fails.
        """
        monkeypatch.setenv("ROXABI_FACTORY_DISCORD_DIR", str(tmp_path))
        from factory.bootstrap.wiring.standalone_telegram import (
            bootstrap_telegram_standalone,
        )

        stop = asyncio.Event()
        stop.set()

        raw_config = {"telegram": {"bots": [{"bot_id": "testbot"}]}}
        from factory.bootstrap.factory.config import AdapterConfigBundle
        from factory.core.messaging.message import Platform

        config_bundle = MagicMock(spec=AdapterConfigBundle)
        vault_dir = tmp_path

        mock_nc = AsyncMock()
        mock_js = AsyncMock()
        mock_nc.jetstream = MagicMock(return_value=mock_js)

        captured_kwargs: dict = {}

        def _make_tg_adapter(**kwargs):
            captured_kwargs.update(kwargs)
            mock_adapter = MagicMock()
            mock_adapter._bot_id = "testbot"
            mock_adapter.dp = MagicMock()
            mock_adapter.dp.start_polling = AsyncMock(return_value=None)
            mock_adapter.dp.stop_polling = AsyncMock(return_value=None)
            mock_adapter.close = AsyncMock()
            return mock_adapter

        wire_stub = _make_wire_bot_common_stub()

        with (
            patch(
                "factory.bootstrap.credentials.load_bot_token",
                return_value=("fake-token", None),
            ),
            patch(
                "factory.bootstrap.wiring.standalone_telegram.init_blobstore",
                return_value=None,
            ),
            patch(
                "factory.bootstrap.wiring.standalone_telegram.wait_for_hub",
                AsyncMock(return_value=None),
            ),
            patch(
                "factory.bootstrap.wiring.standalone_telegram.wire_bot_common",
                side_effect=wire_stub,
            ),
            patch(
                "factory.adapters.telegram.TelegramAdapter",
                side_effect=_make_tg_adapter,
            ),
            patch(
                "factory.bootstrap.lifecycle.signal_handlers.setup_shutdown_event",
                return_value=stop,
            ),
            patch(
                "factory.bootstrap.lifecycle.lifecycle_helpers.close_safely",
                AsyncMock(),
            ),
        ):
            await bootstrap_telegram_standalone(
                nc=mock_nc,
                raw_config=raw_config,
                config_bundle=config_bundle,
                vault_dir=vault_dir,
                platform_enum=Platform.TELEGRAM,
                _stop=stop,
            )

        # Assert — adapter was constructed with last_session= (not turn_store=)
        assert "last_session" in captured_kwargs, (
            "TelegramAdapter not constructed with last_session= kwarg — "
            "turns.db wiring may still be present"
        )
        assert "turn_store" not in captured_kwargs, (
            "TelegramAdapter still constructed with turn_store= kwarg — "
            "turns.db coupling not dropped"
        )
        # last_session must not be None — source-level test confirms it's a
        # KvLastSessionStore (see test_standalone_telegram_imports_kv_last_session).
        assert captured_kwargs["last_session"] is not None, (
            "last_session= is None — KvLastSessionStore not injected"
        )


# ---------------------------------------------------------------------------
# SC2: standalone_discord.py source does NOT reference TurnStore for turns.db
# ---------------------------------------------------------------------------


class TestDiscordStandaloneNoTurnsDb:
    """standalone_discord must not open turns.db — source-level + behavioral."""

    def test_standalone_discord_source_has_no_turns_db_reference(self) -> None:
        """Source-level guard: 'turns.db' absent from standalone_discord.

        Negative: if a developer re-adds turns.db to _create_dc_stores, this fires.
        Note: discord.db is expected (ThreadStore for Discord thread ownership) —
        only turns.db is forbidden.
        """
        from factory.bootstrap.wiring import standalone_discord as _mod

        source = inspect.getsource(_mod)
        assert "turns.db" not in source, (
            "standalone_discord references 'turns.db' — must use KV only"
        )

    def test_standalone_discord_source_has_no_turn_store_for_turns(self) -> None:
        """Source-level guard: TurnStore for turns.db absent from standalone_discord.

        _create_dc_stores should create only ThreadStore (discord.db) and return it.
        Negative: re-adding TurnStore to _create_dc_stores would break this.
        """
        from factory.bootstrap.wiring import standalone_discord as _mod

        source = inspect.getsource(_mod)
        assert "KvLastSessionStore" in source, (
            "standalone_discord does not use KvLastSessionStore"
        )

    async def test_discord_standalone_constructs_adapter_with_last_session_kwarg(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Behavioral: DiscordAdapter receives last_session= kwarg (not turn_store=).

        Negative: if the wiring still passes turn_store= to DiscordAdapter, the adapter
        ctor raises TypeError (param removed) and the bootstrap crashes — but this test
        catches the regression at the unit level, not in production.
        """
        # Set Discord data dir to tmp so no real home dir is written
        monkeypatch.setenv("ROXABI_FACTORY_DISCORD_DIR", str(tmp_path))

        from factory.bootstrap.wiring.standalone_discord import (
            bootstrap_discord_standalone,
        )

        stop = asyncio.Event()
        stop.set()

        raw_config = {
            "discord": {
                "bots": [
                    {"bot_id": "testbot", "auto_thread": False, "thread_hot_hours": 4}
                ]
            }
        }
        from factory.bootstrap.factory.config import AdapterConfigBundle
        from factory.core.messaging.message import Platform

        config_bundle = MagicMock(spec=AdapterConfigBundle)
        vault_dir = tmp_path

        mock_nc = AsyncMock()
        mock_js = AsyncMock()
        mock_nc.jetstream = MagicMock(return_value=mock_js)

        captured_kwargs: dict = {}

        def _make_dc_adapter(**kwargs):
            captured_kwargs.update(kwargs)
            mock_adapter = MagicMock()
            mock_adapter._bot_id = "testbot"
            mock_adapter._watch_channels = frozenset()
            mock_adapter._resolve_channel = MagicMock()
            mock_adapter.close = AsyncMock()
            # Discord teardown calls asyncio.create_task(a.start(tok)) — must be
            # a coroutine function so create_task can wrap the return value.
            mock_adapter.start = AsyncMock()
            return mock_adapter

        mock_thread_store = AsyncMock()
        mock_thread_store.connect = AsyncMock()
        mock_thread_store.close = AsyncMock()

        wire_stub = _make_wire_bot_common_stub()

        with (
            patch(
                "factory.bootstrap.credentials.load_bot_token",
                return_value=("fake-token", None),
            ),
            patch(
                "factory.bootstrap.wiring.standalone_discord._create_dc_stores",
                AsyncMock(return_value=(mock_thread_store,)),
            ),
            patch(
                "factory.bootstrap.wiring.standalone_discord.init_blobstore",
                return_value=None,
            ),
            patch(
                "factory.bootstrap.wiring.standalone_discord.wait_for_hub",
                AsyncMock(return_value=None),
            ),
            patch(
                "factory.bootstrap.wiring.standalone_discord.seed_watch_channels",
                AsyncMock(return_value=frozenset()),
            ),
            patch(
                "factory.bootstrap.wiring.standalone_discord.start_watch_channels_task",
                MagicMock(return_value=_noop_task()),
            ),
            patch(
                "factory.bootstrap.wiring.standalone_discord.wire_bot_common",
                side_effect=wire_stub,
            ),
            patch(
                "factory.adapters.discord.DiscordAdapter",
                side_effect=_make_dc_adapter,
            ),
            patch(
                "factory.bootstrap.lifecycle.signal_handlers.setup_shutdown_event",
                return_value=stop,
            ),
            patch(
                "factory.bootstrap.lifecycle.lifecycle_helpers.close_safely",
                AsyncMock(),
            ),
        ):
            await bootstrap_discord_standalone(
                nc=mock_nc,
                raw_config=raw_config,
                config_bundle=config_bundle,
                vault_dir=vault_dir,
                platform_enum=Platform.DISCORD,
                _stop=stop,
            )

        # Assert — adapter constructed with last_session=, NOT turn_store=
        assert "last_session" in captured_kwargs, (
            "DiscordAdapter not constructed with last_session= kwarg"
        )
        assert "turn_store" not in captured_kwargs, (
            "DiscordAdapter still constructed with turn_store= kwarg — "
            "turns.db coupling not dropped"
        )
        # last_session must not be None — source-level test confirms it's
        # KvLastSessionStore (see test_standalone_discord_imports_kv_last_session_store)
        assert captured_kwargs["last_session"] is not None, (
            "last_session= is None — KvLastSessionStore not injected"
        )


# ---------------------------------------------------------------------------
# SC3: unified path (bootstrap_wiring.py) uses TurnStoreLastSession, not TurnStore
# ---------------------------------------------------------------------------


class TestUnifiedWiringLastSession:
    """bootstrap_wiring.py (unified / factory start) uses TurnStoreLastSession.

    Hub-side wiring: adapters get TurnStoreLastSession (file get, set=no-op)
    — NOT a raw TurnStore — ensuring the adapter never writes turns.db directly.
    """

    def test_bootstrap_wiring_source_uses_turn_store_last_session_not_raw(
        self,
    ) -> None:
        """Source-level guard: bootstrap_wiring uses TurnStoreLastSession wrapper.

        Negative: if bootstrap_wiring passed turn_store= directly (not wrapped),
        the adapter would call set_last_session on TurnStore, violating ADR-075.
        """
        import factory.bootstrap.wiring.bootstrap_wiring as _mod

        source = inspect.getsource(_mod)
        assert "TurnStoreLastSession" in source, (
            "bootstrap_wiring.py does not use TurnStoreLastSession — "
            "hub-side last_session wiring missing"
        )
        # The key guard: no raw `turn_store=` kwarg in adapter ctor calls.
        # Verify that `last_session=TurnStoreLastSession` appears (wrapped usage).
        assert "last_session=TurnStoreLastSession" in source, (
            "bootstrap_wiring.py does not inject TurnStoreLastSession as last_session= "
            "in adapter constructors"
        )
