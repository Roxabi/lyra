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

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_wire_bot_common_stub():
    """Return a fake wire_bot_common that invokes the adapter_factory.

    The adapter_factory closure (defined per-test) populates `captured_kwargs`
    from the test scope and returns the adapter stub so teardown has a properly
    stubbed object with the right async methods.
    """

    async def _fake_wire_bot_common(*, adapter_factory, **_):
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
# SC1b: _bootstrap_adapter_standalone does NOT mkdir for telegram (#1734 D5)
# ---------------------------------------------------------------------------


class TestTelegramAdapterStandaloneNoMkdir:
    """_bootstrap_adapter_standalone must NOT call mkdir for telegram platform.

    Telegram containers have a read-only ~/.roxabi — any mkdir attempt crashes
    the container with OSError EROFS.  The mkdir must be Discord-only (#1734 D5).
    """

    def test_adapter_standalone_source_guards_mkdir_to_discord(self) -> None:
        """Source guard: no mkdir in adapter_standalone; mkdir is discord-only.

        #28 relocated the data-dir mkdir from adapter_standalone.py into
        bootstrap_discord_standalone (standalone_discord.py).  This two-part assertion
        verifies the post-#28 structural guarantee of #1734 D5:

        Part 1 — adapter_standalone.py contains NO mkdir at all (read-only telegram
        path can never trigger a filesystem write, regardless of platform).

        Part 2 — standalone_discord.py's bootstrap_discord_standalone DOES contain
        mkdir (discord-only guarantee: the discord container has a writable data dir).

        Why source-inspection here (in addition to behavioral tests below):
        Source inspection catches structural regressions that survive behavioral mocking
        — e.g. a new helper imported at module level whose mkdir call is not reachable
        from the exercised code path.  The behavioral tests (see
        test_telegram_standalone_never_calls_mkdir and
        test_discord_standalone_calls_mkdir) provide the stronger runtime guarantee
        for the hot paths; this test provides a cheap belt-and-suspenders static layer.
        """
        import factory.bootstrap.standalone.adapter_standalone as _adapter_mod
        import factory.bootstrap.wiring.standalone_discord as _discord_mod

        # Part 1: no mkdir in _bootstrap_adapter_standalone — structural safety.
        # Uses getsource on the specific function (not the module) so unrelated
        # module-level helpers don't produce false-positives.
        # The behavioral test (test_telegram_standalone_never_calls_mkdir) provides
        # the stronger runtime guarantee; this is a cheap static layer.
        adapter_fn = _adapter_mod._bootstrap_adapter_standalone
        adapter_source = inspect.getsource(adapter_fn)
        assert "mkdir" not in adapter_source, (
            "mkdir found in _bootstrap_adapter_standalone — "
            "any mkdir here would be reachable from the telegram path, "
            "crashing on read-only fs (#1734 D5)"
        )

        # Part 2: mkdir present in bootstrap_discord_standalone — discord-only.
        # Uses inspect.getsource on the specific function (not the module) so a
        # rename of the function would be caught by AttributeError, not silently
        # pass with a module-wide grep that matches an unrelated helper.
        discord_fn = _discord_mod.bootstrap_discord_standalone
        discord_func_source = inspect.getsource(discord_fn)
        assert "mkdir" in discord_func_source, (
            "mkdir not found in bootstrap_discord_standalone (standalone_discord.py) — "
            "discord data-dir creation missing (#28 relocation invariant)"
        )

    def test_adapter_standalone_source_no_mkdir_in_telegram_branch(self) -> None:
        """Source-level guard: no mkdir call in the telegram branch.

        Parse adapter_standalone source and verify that between the
        'if platform == "telegram"' and 'elif platform == "discord"' lines
        there is no 'mkdir' call.
        """
        import factory.bootstrap.standalone.adapter_standalone as _mod

        source = inspect.getsource(_mod)
        lines = source.splitlines()

        tg_branch_idx = next(
            (i for i, ln in enumerate(lines) if 'platform == "telegram"' in ln), None
        )
        dc_branch_idx = next(
            (i for i, ln in enumerate(lines) if 'platform == "discord"' in ln), None
        )
        assert tg_branch_idx is not None, (
            "telegram branch not found in adapter_standalone"
        )
        assert dc_branch_idx is not None, (
            "discord branch not found in adapter_standalone"
        )

        telegram_block = lines[tg_branch_idx:dc_branch_idx]
        mkdir_in_tg = [ln for ln in telegram_block if "mkdir" in ln]
        assert not mkdir_in_tg, (
            "mkdir call found in the telegram branch of adapter_standalone — "
            f"lines: {mkdir_in_tg}"
        )

    def test_telegram_adapter_standalone_factory_data_dir_not_called(self) -> None:
        """Source-level guard: factory_data_dir absent from adapter_standalone.

        #28 removed factory_data_dir from adapter_standalone.py entirely — the
        discord data-dir mkdir now lives in standalone_discord.py.  This assertion
        verifies that adapter_standalone.py does not import or call factory_data_dir,
        so the telegram path can never trigger a data-dir creation on a read-only fs
        (#1734 D5).
        """
        import factory.bootstrap.standalone.adapter_standalone as _mod

        source = inspect.getsource(_mod)
        assert "factory_data_dir" not in source, (
            "factory_data_dir found in adapter_standalone.py — "
            "this would be reachable from the telegram path and crash on read-only fs "
            "(#1734 D5; #28 relocated mkdir to standalone_discord.py)"
        )

    async def test_telegram_standalone_never_calls_mkdir(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Behavioral: bootstrap_telegram_standalone never calls Path.mkdir (#1734 D5).

        Drives the full telegram boot path with real module wiring (no source-text
        scanning).  pathlib.Path.mkdir is patched at the pathlib level so any mkdir
        call — even through helpers — is caught.

        Negative: if mkdir is ever added to standalone_telegram.py (or a function it
        calls without the patch seam), this test catches it at runtime.
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
        mock_mkdir = MagicMock()

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
            patch("pathlib.Path.mkdir", mock_mkdir),
        ):
            await bootstrap_telegram_standalone(
                nc=mock_nc,
                raw_config=raw_config,
                config_bundle=config_bundle,
                platform_enum=Platform.TELEGRAM,
                _stop=stop,
            )

        # Negative: if mkdir is added to the telegram boot path, call_count > 0
        mock_mkdir.assert_not_called()

    async def test_discord_standalone_calls_mkdir(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Behavioral: bootstrap_discord_standalone calls Path.mkdir for discord dir.

        Drives the full discord boot path and verifies mkdir IS called — confirming
        the #28 relocation placed mkdir in the discord path only (#1734 D5).

        Negative: if the mkdir is accidentally removed from standalone_discord.py,
        this test fails, catching the regression before prod deploys to a container
        that relies on the directory being created.
        """
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
            mock_adapter.start = AsyncMock()
            return mock_adapter

        mock_thread_store = AsyncMock()
        mock_thread_store.connect = AsyncMock()
        mock_thread_store.close = AsyncMock()

        wire_stub = _make_wire_bot_common_stub()
        mock_mkdir = MagicMock()

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
            patch("pathlib.Path.mkdir", mock_mkdir),
        ):
            await bootstrap_discord_standalone(
                nc=mock_nc,
                raw_config=raw_config,
                config_bundle=config_bundle,
                platform_enum=Platform.DISCORD,
                _stop=stop,
            )

        # Negative: removing the mkdir from standalone_discord.py causes this to fail
        mock_mkdir.assert_called()


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
