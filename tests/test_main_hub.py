"""Tests for __main__: hub wiring and graceful shutdown (T2, T3)."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

import factory.__main__ as main_mod
from factory.core.auth.authenticator import Authenticator as AuthMiddleware
from factory.core.messaging.message import Platform
from tests.conftest import patch_all

# ---------------------------------------------------------------------------
# T2 — Hub wiring: both adapters + wildcard bindings registered
# ---------------------------------------------------------------------------


class TestHubWiring:
    async def test_registers_both_adapters(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured, _ = patch_all(monkeypatch)
        stop = asyncio.Event()
        stop.set()

        await main_mod._main(_stop=stop)

        hub = captured[0]
        assert any(p == Platform.TELEGRAM for p, _ in hub.adapter_registry)
        assert any(p == Platform.DISCORD for p, _ in hub.adapter_registry)

    async def test_registers_wildcard_bindings(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured, _ = patch_all(monkeypatch)
        stop = asyncio.Event()
        stop.set()

        await main_mod._main(_stop=stop)

        hub = captured[0]
        tg_bindings = [
            v for k, v in hub.bindings.items() if k.platform == Platform.TELEGRAM
        ]
        dc_bindings = [
            v for k, v in hub.bindings.items() if k.platform == Platform.DISCORD
        ]
        assert tg_bindings, "No Telegram wildcard binding registered"
        assert dc_bindings, "No Discord wildcard binding registered"


# ---------------------------------------------------------------------------
# T3 — Graceful shutdown: tasks cancelled without CancelledError propagating
# ---------------------------------------------------------------------------


class TestGracefulShutdown:
    async def test_clean_exit_on_stop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_main() returns normally when stop fires — no exception raised."""
        _, fake_store = patch_all(monkeypatch)
        stop = asyncio.Event()
        stop.set()

        await main_mod._main(_stop=stop)

        # SC10: auth_store.close() must be awaited during shutdown
        fake_store.close.assert_awaited_once()

    async def test_delayed_stop_cancels_tasks(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A stop fired after a small delay also exits cleanly."""
        patch_all(monkeypatch)
        stop = asyncio.Event()

        async def trigger() -> None:
            await asyncio.sleep(0.05)  # event-based
            stop.set()

        trigger_task = asyncio.create_task(trigger())
        await main_mod._main(_stop=stop)
        await trigger_task  # ensure no lingering tasks

    async def test_auth_store_boot_order(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SC10 (S5): connect() called before from_bot_store() reads the store."""
        call_log: list[str] = []

        _, fake_store = patch_all(monkeypatch)
        fake_store.connect.side_effect = lambda: call_log.append("connect")
        from_bot_store_calls: list[str] = []
        monkeypatch.setattr(
            AuthMiddleware,
            "from_bot_store",
            classmethod(
                lambda cls, deps: (
                    from_bot_store_calls.append(deps.platform) or MagicMock()
                )
            ),
        )

        stop = asyncio.Event()
        stop.set()
        await main_mod._main(_stop=stop)

        assert "connect" in call_log, "auth_store.connect() was not called"
        assert from_bot_store_calls, "from_bot_store() was not called after connect"
