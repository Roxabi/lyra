"""Tests for OutboundAdapterBase ABC.

RED-phase tests: the module under test (lyra.adapters._base_outbound) does not
exist yet. All tests are expected to fail with ImportError until V3 is implemented.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.adapters._base_outbound import OutboundAdapterBase
from lyra.adapters._shared_streaming import PlatformCallbacks
from lyra.core.message import InboundMessage, OutboundMessage
from lyra.core.render_events import RenderEvent, TextRenderEvent
from tests.adapters.conftest import make_tg_msg

# ---------------------------------------------------------------------------
# Concrete subclasses for testing
# ---------------------------------------------------------------------------


class ConcreteAdapter(OutboundAdapterBase):
    """Minimal concrete subclass that implements all abstract methods."""

    async def send(
        self, original_msg: InboundMessage, outbound: OutboundMessage
    ) -> None:  # noqa: E501
        pass

    def _make_streaming_callbacks(
        self, original_msg: InboundMessage, outbound: OutboundMessage | None
    ) -> PlatformCallbacks:
        return MagicMock(spec=PlatformCallbacks)

    def _start_typing(self, scope_id: int) -> None:
        pass

    def _cancel_typing(self, scope_id: int) -> None:
        pass


class TestableAdapter(OutboundAdapterBase):
    """Concrete subclass that returns real PlatformCallbacks with AsyncMock callbacks.

    Used for send_streaming() integration tests where StreamingSession.run()
    must not crash.
    """

    async def send(
        self, original_msg: InboundMessage, outbound: OutboundMessage
    ) -> None:  # noqa: E501
        pass

    def _make_streaming_callbacks(
        self, original_msg: InboundMessage, outbound: OutboundMessage | None
    ) -> PlatformCallbacks:
        return PlatformCallbacks(
            send_placeholder=AsyncMock(return_value=(MagicMock(), 42)),
            edit_placeholder_text=AsyncMock(),
            edit_placeholder_tool=AsyncMock(),
            send_message=AsyncMock(return_value=99),
            send_fallback=AsyncMock(return_value=77),
            chunk_text=lambda text: [text],
            start_typing=MagicMock(),
            cancel_typing=MagicMock(),
            get_msg=MagicMock(side_effect=lambda key, fb: fb),
            placeholder_text="\u2026",
        )

    def _start_typing(self, scope_id: int) -> None:
        pass

    def _cancel_typing(self, scope_id: int) -> None:
        pass


# ---------------------------------------------------------------------------
# Async event helpers
# ---------------------------------------------------------------------------


async def _events() -> AsyncIterator[RenderEvent]:
    yield TextRenderEvent(text="hello", is_final=True)


# ---------------------------------------------------------------------------
# TestOutboundAdapterBaseABC
# ---------------------------------------------------------------------------


class TestOutboundAdapterBaseABC:
    def test_missing_send_raises_type_error(self) -> None:
        """Instantiating a subclass that omits send() must raise TypeError."""

        # Arrange
        class MissingSend(OutboundAdapterBase):
            def _make_streaming_callbacks(self, original_msg, outbound):  # type: ignore[override]
                pass

            def _start_typing(self, scope_id):
                pass

            def _cancel_typing(self, scope_id):
                pass

        # Act / Assert
        with pytest.raises(TypeError):
            MissingSend()  # type: ignore[abstract]

    def test_missing_make_streaming_callbacks_raises_type_error(self) -> None:
        """Instantiating a subclass that omits _make_streaming_callbacks() must raise TypeError."""  # noqa: E501

        # Arrange
        class MissingCallbacks(OutboundAdapterBase):
            async def send(self, original_msg, outbound):
                pass

            def _start_typing(self, scope_id):
                pass

            def _cancel_typing(self, scope_id):
                pass

        # Act / Assert
        with pytest.raises(TypeError):
            MissingCallbacks()  # type: ignore[abstract]

    def test_missing_start_typing_raises_type_error(self) -> None:
        """Instantiating a subclass that omits _start_typing() must raise TypeError."""

        # Arrange
        class MissingStartTyping(OutboundAdapterBase):
            async def send(self, original_msg, outbound):
                pass

            def _make_streaming_callbacks(self, original_msg, outbound):  # type: ignore[override]
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

            def _make_streaming_callbacks(self, original_msg, outbound):  # type: ignore[override]
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

        # Assert — StreamingSession should have stored the placeholder message id
        assert "reply_message_id" in outbound.metadata

    async def test_send_streaming_calls_make_streaming_callbacks(self) -> None:
        """send_streaming() must call _make_streaming_callbacks() exactly once."""
        # Arrange
        call_count = 0
        original_msg = make_tg_msg()

        class TrackingAdapter(OutboundAdapterBase):
            async def send(self, original_msg, outbound):
                pass

            def _make_streaming_callbacks(self, original_msg, outbound):
                nonlocal call_count
                call_count += 1
                return PlatformCallbacks(
                    send_placeholder=AsyncMock(return_value=(MagicMock(), 42)),
                    edit_placeholder_text=AsyncMock(),
                    edit_placeholder_tool=AsyncMock(),
                    send_message=AsyncMock(return_value=99),
                    send_fallback=AsyncMock(return_value=77),
                    chunk_text=lambda text: [text],
                    start_typing=MagicMock(),
                    cancel_typing=MagicMock(),
                    get_msg=MagicMock(side_effect=lambda key, fb: fb),
                    placeholder_text="\u2026",
                )

            def _start_typing(self, scope_id):
                pass

            def _cancel_typing(self, scope_id):
                pass

        adapter = TrackingAdapter()

        # Act
        await adapter.send_streaming(original_msg, _events(), outbound=None)

        # Assert
        assert call_count == 1
