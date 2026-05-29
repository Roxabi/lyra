"""Tests for OutboundAdapterBase ABC.

Migrated in S7 (#1501): PlatformCallbacks removed; _make_streaming_callbacks
is no longer abstract. Tests now use MagicMock formatter directly.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.adapters.shared._base_outbound import OutboundAdapterBase
from lyra.core.messaging.message import InboundMessage, OutboundMessage
from lyra.core.messaging.render_events import RenderEvent, TextEndRenderEvent
from lyra.outbound.emitter import OutboundEmitter
from tests.adapters.conftest import make_tg_msg

# ---------------------------------------------------------------------------
# Shared formatter helper
# ---------------------------------------------------------------------------


def _make_test_formatter(**overrides) -> MagicMock:
    """Build a mock OutboundFormatter suitable for OutboundEmitter construction."""
    fmt = MagicMock()
    fmt.placeholder_text = MagicMock(return_value="…")
    fmt.chunk = MagicMock(side_effect=lambda t: [t] if t else [])
    fmt.get_msg = MagicMock(side_effect=lambda key, fallback: fallback)
    fmt.send_placeholder = AsyncMock(return_value=(MagicMock(), 42))
    fmt.edit_placeholder_text = AsyncMock()
    fmt.send_trace_placeholder = AsyncMock(return_value=(object(), 42))
    fmt.send_message = AsyncMock(return_value=99)
    fmt.send_fallback = AsyncMock(return_value=77)
    fmt.edit_reasoning = AsyncMock()
    fmt.edit_tool_recap = AsyncMock()
    for k, v in overrides.items():
        setattr(fmt, k, v)
    return fmt


# ---------------------------------------------------------------------------
# Concrete subclasses for testing
# ---------------------------------------------------------------------------


class ConcreteAdapter(OutboundAdapterBase):
    """Minimal concrete subclass that implements all abstract methods."""

    async def send(
        self, original_msg: InboundMessage, outbound: OutboundMessage
    ) -> None:
        pass

    def _make_emitter(
        self, original_msg: InboundMessage, outbound: OutboundMessage | None
    ) -> OutboundEmitter:
        return OutboundEmitter(_make_test_formatter(), outbound)

    def _start_typing(self, scope_id: int) -> None:
        pass

    def _cancel_typing(self, scope_id: int) -> None:
        pass


class TestableAdapter(OutboundAdapterBase):
    """Concrete subclass that returns real formatter mocks.

    Used for send_streaming() integration tests where OutboundEmitter.run()
    must not crash.
    """

    async def send(
        self, original_msg: InboundMessage, outbound: OutboundMessage
    ) -> None:
        pass

    def _make_emitter(
        self, original_msg: InboundMessage, outbound: OutboundMessage | None
    ) -> OutboundEmitter:
        return OutboundEmitter(_make_test_formatter(), outbound)

    def _start_typing(self, scope_id: int) -> None:
        pass

    def _cancel_typing(self, scope_id: int) -> None:
        pass


# ---------------------------------------------------------------------------
# Async event helpers
# ---------------------------------------------------------------------------


async def _events() -> AsyncIterator[RenderEvent]:
    yield TextEndRenderEvent(message_id="msg-test-001")


# ---------------------------------------------------------------------------
# TestOutboundAdapterBaseABC
# ---------------------------------------------------------------------------


class TestOutboundAdapterBaseABC:
    def test_missing_send_raises_type_error(self) -> None:
        """Instantiating a subclass that omits send() must raise TypeError."""

        # Arrange
        class MissingSend(OutboundAdapterBase):
            def _make_emitter(self, original_msg, outbound):  # type: ignore[override]
                pass

            def _start_typing(self, scope_id):
                pass

            def _cancel_typing(self, scope_id):
                pass

        # Act / Assert
        with pytest.raises(TypeError):
            MissingSend()  # type: ignore[abstract]

    def test_missing_make_emitter_raises_type_error(self) -> None:
        """Instantiating a subclass that omits _make_emitter() must raise TypeError."""

        # Arrange
        class MissingEmitter(OutboundAdapterBase):
            async def send(self, original_msg, outbound):
                pass

            def _start_typing(self, scope_id):
                pass

            def _cancel_typing(self, scope_id):
                pass

        # Act / Assert
        with pytest.raises(TypeError):
            MissingEmitter()  # type: ignore[abstract]

    def test_missing_start_typing_raises_type_error(self) -> None:
        """Instantiating a subclass that omits _start_typing() must raise TypeError."""

        # Arrange
        class MissingStartTyping(OutboundAdapterBase):
            async def send(self, original_msg, outbound):
                pass

            def _make_emitter(self, original_msg, outbound):  # type: ignore[override]
                pass

            def _cancel_typing(self, scope_id):
                pass

        # Act / Assert
        with pytest.raises(TypeError):
            MissingStartTyping()  # type: ignore[abstract]

    def test_missing_cancel_typing_raises_type_error(self) -> None:
        """Instantiating a subclass that omits _cancel_typing() must raise TypeError."""

        # Arrange
        class MissingCancelTyping(OutboundAdapterBase):
            async def send(self, original_msg, outbound):
                pass

            def _make_emitter(self, original_msg, outbound):  # type: ignore[override]
                pass

            def _start_typing(self, scope_id):
                pass

        # Act / Assert
        with pytest.raises(TypeError):
            MissingCancelTyping()  # type: ignore[abstract]

    def test_concrete_subclass_instantiates_successfully(self) -> None:
        """A complete concrete subclass must instantiate without error."""
        # Act
        adapter = ConcreteAdapter()

        # Assert
        assert adapter is not None

    def test_init_takes_no_arguments(self) -> None:
        """__init__ must accept no arguments (cooperative MRO safety for discord.Client)."""  # noqa: E501
        # Act / Assert — no TypeError from unexpected kwargs
        adapter = ConcreteAdapter()
        assert isinstance(adapter, OutboundAdapterBase)


# ---------------------------------------------------------------------------
# TestOutboundAdapterBaseSendStreaming
# ---------------------------------------------------------------------------


class TestOutboundAdapterBaseSendStreaming:
    async def test_send_streaming_delegates_to_session(self) -> None:
        """send_streaming() must complete without raising when events are produced."""
        # Arrange
        adapter = TestableAdapter()
        original_msg = make_tg_msg()

        # Act / Assert — no exception raised
        await adapter.send_streaming(original_msg, _events(), outbound=None)

    async def test_send_streaming_none_outbound_no_crash(self) -> None:
        """send_streaming() with outbound=None must not raise."""
        # Arrange
        adapter = TestableAdapter()
        original_msg = make_tg_msg()

        # Act / Assert
        await adapter.send_streaming(original_msg, _events(), outbound=None)

    async def test_send_streaming_with_outbound_updates_metadata(self) -> None:
        """send_streaming() with a real OutboundMessage stores reply_message_id."""
        # Arrange
        adapter = TestableAdapter()
        original_msg = make_tg_msg()
        outbound = OutboundMessage.from_text("")

        # Act
        await adapter.send_streaming(original_msg, _events(), outbound=outbound)

        # Assert — OutboundEmitter should have stored the placeholder message id
        assert "reply_message_id" in outbound.metadata

    async def test_send_streaming_calls_make_emitter_exactly_once(self) -> None:
        """send_streaming() must call _make_emitter() exactly once.

        This pins the base class dispatch contract — send_streaming delegates
        to _make_emitter (formatter-based path).
        """
        # Arrange
        emitter_call_count = 0
        original_msg = make_tg_msg()

        class TrackingAdapter(OutboundAdapterBase):
            async def send(self, original_msg, outbound):
                pass

            def _make_emitter(self, original_msg, outbound):
                nonlocal emitter_call_count
                emitter_call_count += 1
                return OutboundEmitter(_make_test_formatter(), outbound)

            def _start_typing(self, scope_id):
                pass

            def _cancel_typing(self, scope_id):
                pass

        adapter = TrackingAdapter()

        # Act
        await adapter.send_streaming(original_msg, _events(), outbound=None)

        # Assert — send_streaming → _make_emitter dispatch fires exactly once
        assert emitter_call_count == 1
