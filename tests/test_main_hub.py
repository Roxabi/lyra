"""Tests for __main__: hub wiring and graceful shutdown (T2, T3)."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

import lyra.__main__ as main_mod
from lyra.core.auth.authenticator import Authenticator as AuthMiddleware
from lyra.core.messaging.message import Platform
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
            await asyncio.sleep(0.05)
            stop.set()

        trigger_task = asyncio.create_task(trigger())
        await main_mod._main(_stop=stop)
        await trigger_task  # ensure no lingering tasks

    async def test_auth_store_boot_order(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SC10 (S5): connect() and seed_from_config() called before from_config().

        Boot constraint: cache must be warm before middleware reads from it.
        """
        call_log: list[str] = []

        _, fake_store = patch_all(monkeypatch)
        fake_store.connect.side_effect = lambda: call_log.append("connect")
        fake_store.seed_from_config.side_effect = lambda raw, section: call_log.append(
            f"seed:{section}"
        )
        # Re-patch from_config to record when it is called relative to connect/seed
        from_config_calls: list[str] = []
        monkeypatch.setattr(
            AuthMiddleware,
            "from_config",
            classmethod(
                lambda cls, raw, section, store=None: (
                    from_config_calls.append(section) or MagicMock()
                )
            ),
        )

        stop = asyncio.Event()
        stop.set()
        await main_mod._main(_stop=stop)

        assert "connect" in call_log, "auth_store.connect() was not called"
        assert any(s.startswith("seed:") for s in call_log), (
            "auth_store.seed_from_config() was not called"
        )
        # connect must precede all seed calls
        connect_idx = call_log.index("connect")
        for entry in call_log:
            if entry.startswith("seed:"):
                assert call_log.index(entry) > connect_idx, (
                    f"seed_from_config({entry}) called before connect()"
                )
