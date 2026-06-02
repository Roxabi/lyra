"""Unit tests for _handle_post_send extracted from dispatch_outbound_item.

Targets (from _dispatch.py lines 164-194):
- callback invocation with correct target (payload for send, outbound for others)
- circuit failure recording when exc is set
- iterator drainage on error for streaming/audio_stream/voice_stream
- user notification on delivery failure for send/streaming
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from factory.core.hub.outbound._dispatch import _handle_post_send
from factory.core.hub.outbound.outbound_errors import _SEND_ERROR_MSG
from factory.core.messaging.message import OutboundMessage
from factory.core.messaging.utils.callbacks import TrustedCallback
from tests.core.conftest import make_dispatcher_msg

# ---------------------------------------------------------------------------
# Callback invocation
# ---------------------------------------------------------------------------


class TestHandlePostSendCallback:
    async def test_handle_post_send_callback_invoked_with_payload_for_send(
        self,
    ) -> None:
        """_on_dispatched receives payload (OutboundMessage) when kind is send."""
        msg = make_dispatcher_msg()
        out = OutboundMessage.from_text("hi")
        called_with: list[OutboundMessage] = []
        out.metadata["_on_dispatched"] = TrustedCallback(called_with.append)

        await _handle_post_send(
            kind="send",
            payload=out,
            outbound=None,
            msg=msg,
            platform_name="telegram",
            circuit=None,
            try_notify_fn=AsyncMock(),
            _last_exc=None,
        )

        assert called_with == [out]
        assert "_on_dispatched" not in out.metadata  # pop=True

    async def test_handle_post_send_callback_invoked_with_outbound_for_streaming(
        self,
    ) -> None:
        """_on_dispatched receives outbound when kind is streaming."""
        msg = make_dispatcher_msg()
        out = OutboundMessage.from_text("stream")
        called_with: list[OutboundMessage] = []
        out.metadata["_on_dispatched"] = TrustedCallback(called_with.append)

        async def _iter() -> AsyncIterator[str]:
            yield "chunk"

        await _handle_post_send(
            kind="streaming",
            payload=_iter(),
            outbound=out,
            msg=msg,
            platform_name="telegram",
            circuit=None,
            try_notify_fn=AsyncMock(),
            _last_exc=None,
        )

        assert called_with == [out]
        assert "_on_dispatched" not in out.metadata  # pop=True

    async def test_handle_post_send_callback_not_called_when_outbound_is_none(
        self,
    ) -> None:
        """If outbound is None and kind != send, callback is skipped."""
        msg = make_dispatcher_msg()
        notify = AsyncMock()

        await _handle_post_send(
            kind="audio",
            payload=b"audio_bytes",
            outbound=None,
            msg=msg,
            platform_name="telegram",
            circuit=None,
            try_notify_fn=notify,
            _last_exc=None,
        )

        notify.assert_not_awaited()


# ---------------------------------------------------------------------------
# Circuit failure recording
# ---------------------------------------------------------------------------


class TestHandlePostSendCircuit:
    async def test_handle_post_send_circuit_failure_recorded_when_exc_set(self) -> None:
        """circuit.record_failure() is called when _last_exc is not None."""
        msg = make_dispatcher_msg()
        circuit = MagicMock()

        await _handle_post_send(
            kind="send",
            payload=OutboundMessage.from_text("hi"),
            outbound=None,
            msg=msg,
            platform_name="telegram",
            circuit=circuit,
            try_notify_fn=AsyncMock(),
            _last_exc=ValueError("boom"),
        )

        circuit.record_failure.assert_called_once()

    async def test_handle_post_send_circuit_not_touched_when_no_exc(self) -> None:
        """circuit is never accessed when _last_exc is None."""
        msg = make_dispatcher_msg()
        circuit = MagicMock()

        await _handle_post_send(
            kind="send",
            payload=OutboundMessage.from_text("hi"),
            outbound=None,
            msg=msg,
            platform_name="telegram",
            circuit=circuit,
            try_notify_fn=AsyncMock(),
            _last_exc=None,
        )

        circuit.assert_not_called()
        circuit.record_failure.assert_not_called()


# ---------------------------------------------------------------------------
# Iterator drainage on error
# ---------------------------------------------------------------------------


class TestHandlePostSendDrain:
    @pytest.mark.parametrize("kind", ["streaming", "audio_stream", "voice_stream"])
    async def test_handle_post_send_iterator_drained_on_error(self, kind: str) -> None:
        """streaming/audio_stream/voice_stream iterators are drained
        when _last_exc is set."""
        msg = make_dispatcher_msg()
        drained = False

        async def _make_iter() -> AsyncIterator[str]:
            nonlocal drained
            drained = True
            yield "chunk1"
            yield "chunk2"

        await _handle_post_send(
            kind=kind,
            payload=_make_iter(),
            outbound=None,
            msg=msg,
            platform_name="telegram",
            circuit=None,
            try_notify_fn=AsyncMock(),
            _last_exc=ValueError("boom"),
        )

        assert drained, f"Iterator not drained for kind={kind}"

    async def test_handle_post_send_iterator_not_drained_when_no_error(self) -> None:
        """Iterator is left untouched when _last_exc is None."""
        msg = make_dispatcher_msg()
        yielded = False

        async def _iter() -> AsyncIterator[str]:
            nonlocal yielded
            yielded = True
            yield "chunk"

        await _handle_post_send(
            kind="streaming",
            payload=_iter(),
            outbound=OutboundMessage.from_text("stream"),
            msg=msg,
            platform_name="telegram",
            circuit=None,
            try_notify_fn=AsyncMock(),
            _last_exc=None,
        )

        assert not yielded, "Iterator should not be consumed when there is no error"


# ---------------------------------------------------------------------------
# User notification on delivery failure
# ---------------------------------------------------------------------------


class TestHandlePostSendNotification:
    @pytest.mark.parametrize("kind", ["send", "streaming"])
    async def test_handle_post_send_user_notified_on_delivery_failure(
        self, kind: str
    ) -> None:
        """try_notify_fn is called for send and streaming kinds when delivery fails."""
        msg = make_dispatcher_msg()
        notify = AsyncMock()

        async def _iter() -> AsyncIterator[str]:
            yield "chunk"

        payload: object = OutboundMessage.from_text("hi") if kind == "send" else _iter()

        await _handle_post_send(
            kind=kind,
            payload=payload,
            outbound=None if kind == "send" else OutboundMessage.from_text("stream"),
            msg=msg,
            platform_name="telegram",
            circuit=None,
            try_notify_fn=notify,
            _last_exc=ValueError("boom"),
        )

        notify.assert_awaited_once_with(msg, _SEND_ERROR_MSG)

    @pytest.mark.parametrize("kind", ["audio_stream", "voice_stream"])
    async def test_handle_post_send_user_not_notified_for_non_message_streams(
        self, kind: str
    ) -> None:
        """try_notify_fn is NOT called for audio_stream/voice_stream on failure."""
        msg = make_dispatcher_msg()
        notify = AsyncMock()

        async def _iter() -> AsyncIterator[str]:
            yield "chunk"

        await _handle_post_send(
            kind=kind,
            payload=_iter(),
            outbound=None,
            msg=msg,
            platform_name="telegram",
            circuit=None,
            try_notify_fn=notify,
            _last_exc=ValueError("boom"),
        )

        notify.assert_not_awaited()

    async def test_handle_post_send_user_not_notified_when_no_error(self) -> None:
        """try_notify_fn is skipped when _last_exc is None."""
        msg = make_dispatcher_msg()
        notify = AsyncMock()

        await _handle_post_send(
            kind="send",
            payload=OutboundMessage.from_text("hi"),
            outbound=None,
            msg=msg,
            platform_name="telegram",
            circuit=None,
            try_notify_fn=notify,
            _last_exc=None,
        )

        notify.assert_not_awaited()
