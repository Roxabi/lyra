"""Tests for Hub bus injection and bot_id propagation to Bus.register().

Slice 1 of issue #534: Hub.__init__ no longer accepts inbound_audio_bus.
Tests for that removed attribute have been deleted.  This file now covers
only the still-live inbound_bus injection path and register_adapter wiring.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from lyra.core.bus import Bus
from lyra.core.hub import Hub
from lyra.core.inbound_bus import LocalBus
from lyra.core.message import InboundMessage, Platform
from tests.core.conftest import MockAdapter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_bus() -> MagicMock:
    """Return a MagicMock that satisfies the Bus[T] Protocol."""
    bus = MagicMock(spec=Bus)
    bus.registered_platforms.return_value = frozenset()
    return bus


# ---------------------------------------------------------------------------
# Default construction — LocalBus created internally
# ---------------------------------------------------------------------------


class TestHubDefaultBus:
    def test_default_inbound_bus_is_local_bus(self) -> None:
        hub = Hub()
        assert isinstance(hub.inbound_bus, LocalBus)

    def test_no_bus_args_backward_compat(self) -> None:
        """Hub() with no bus args must work identically to before the change."""
        hub = Hub()
        hub.register_adapter(Platform.TELEGRAM, "main", MockAdapter())
        assert Platform.TELEGRAM in hub.inbound_bus.registered_platforms()


# ---------------------------------------------------------------------------
# Injected bus — Hub uses the provided instance
# ---------------------------------------------------------------------------


class TestHubInjectedBus:
    def test_injected_inbound_bus_is_used(self) -> None:
        mock_bus: Any = _make_mock_bus()
        hub = Hub(inbound_bus=mock_bus)
        assert hub.inbound_bus is mock_bus

    def test_injected_bus_not_overwritten_by_local_bus(self) -> None:
        mock_bus: Any = _make_mock_bus()
        hub = Hub(inbound_bus=mock_bus)
        # Must remain the injected instance — not replaced by a LocalBus
        assert not isinstance(hub.inbound_bus, LocalBus)


# ---------------------------------------------------------------------------
# register_adapter passes bot_id to bus.register()
# ---------------------------------------------------------------------------


class TestRegisterAdapterBotId:
    def test_register_passes_bot_id_to_inbound_bus(self) -> None:
        mock_bus: Any = _make_mock_bus()
        hub = Hub(inbound_bus=mock_bus)
        hub.register_adapter(Platform.TELEGRAM, "mybot", MockAdapter())
        mock_bus.register.assert_called_once_with(
            Platform.TELEGRAM,
            maxsize=Hub.PLATFORM_QUEUE_MAXSIZE,
            bot_id="mybot",
        )

    def test_register_always_called_bus_handles_idempotency(self) -> None:
        mock_bus: Any = _make_mock_bus()
        hub = Hub(inbound_bus=mock_bus)
        # Hub always calls register(); Bus is responsible for idempotency.
        hub.register_adapter(Platform.TELEGRAM, "bot1", MockAdapter())
        mock_bus.register.assert_called_once_with(
            Platform.TELEGRAM,
            maxsize=Hub.PLATFORM_QUEUE_MAXSIZE,
            bot_id="bot1",
        )

    def test_second_bot_same_platform_calls_register_twice(self) -> None:
        """Hub always calls bus.register() — Bus handles idempotency internally."""
        mock_bus: Any = _make_mock_bus()
        hub = Hub(inbound_bus=mock_bus)
        hub.register_adapter(Platform.TELEGRAM, "bot1", MockAdapter())
        hub.register_adapter(Platform.TELEGRAM, "bot2", MockAdapter())
        assert mock_bus.register.call_count == 2
        mock_bus.register.assert_any_call(
            Platform.TELEGRAM, maxsize=Hub.PLATFORM_QUEUE_MAXSIZE, bot_id="bot1"
        )
        mock_bus.register.assert_any_call(
            Platform.TELEGRAM, maxsize=Hub.PLATFORM_QUEUE_MAXSIZE, bot_id="bot2"
        )


# ---------------------------------------------------------------------------
# LocalBus.register accepts and ignores bot_id
# ---------------------------------------------------------------------------


class TestLocalBusRegisterBotId:
    def test_local_bus_accepts_bot_id_kwarg(self) -> None:
        bus: LocalBus[InboundMessage] = LocalBus(name="test")
        # Must not raise when bot_id is supplied.
        bus.register(Platform.TELEGRAM, maxsize=50, bot_id="anybot")
        assert Platform.TELEGRAM in bus.registered_platforms()

    def test_local_bus_bot_id_none_works(self) -> None:
        bus: LocalBus[InboundMessage] = LocalBus(name="test")
        bus.register(Platform.DISCORD, bot_id=None)
        assert Platform.DISCORD in bus.registered_platforms()
