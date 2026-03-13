"""Tests for Hub.run() audio dispatch via Response.audio (#167 V2).

Verifies that Hub.run() correctly dispatches audio from Response.audio
when the pipeline returns a COMMAND_HANDLED result containing a Response
with an audio field set.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from lyra.core.hub import Action, Hub, MessagePipeline, PipelineResult
from lyra.core.message import InboundMessage, OutboundAudio, Response
from tests.core.conftest import make_inbound_message

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pipeline_result(response: Response) -> PipelineResult:
    """Return a COMMAND_HANDLED PipelineResult wrapping *response*."""
    return PipelineResult(action=Action.COMMAND_HANDLED, response=response)


async def _run_hub_one_msg(hub: Hub, msg: InboundMessage) -> None:
    """Put *msg* on the bus and run Hub.run() until it processes one message."""
    await hub.bus.put(msg)
    try:
        await asyncio.wait_for(hub.run(), timeout=0.3)
    except asyncio.TimeoutError:
        pass  # expected — Hub.run() never returns on its own


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHubRunAudioDispatch:
    """Hub.run() dispatches audio correctly based on Response.audio and .content."""

    async def test_audio_only_response_dispatches_audio(self) -> None:
        """Audio-only Response: dispatch_audio() called, not dispatch_response()."""
        hub = Hub()
        msg = make_inbound_message(platform="telegram", bot_id="main")
        audio = OutboundAudio(audio_bytes=b"ogg_data", mime_type="audio/ogg")
        response = Response(content="", audio=audio)
        pipeline_result = _make_pipeline_result(response)

        hub.dispatch_audio = AsyncMock()
        hub.dispatch_response = AsyncMock()

        mock = AsyncMock(return_value=pipeline_result)
        with patch.object(MessagePipeline, "process", new=mock):
            await _run_hub_one_msg(hub, msg)

        hub.dispatch_audio.assert_awaited_once_with(msg, audio)
        hub.dispatch_response.assert_not_awaited()

    async def test_text_only_response_dispatches_text(self) -> None:
        """Text-only Response: dispatch_response() called, not dispatch_audio()."""
        hub = Hub()
        msg = make_inbound_message(platform="telegram", bot_id="main")
        response = Response(content="hello")
        pipeline_result = _make_pipeline_result(response)

        hub.dispatch_audio = AsyncMock()
        hub.dispatch_response = AsyncMock()

        mock = AsyncMock(return_value=pipeline_result)
        with patch.object(MessagePipeline, "process", new=mock):
            await _run_hub_one_msg(hub, msg)

        hub.dispatch_response.assert_awaited_once_with(msg, response)
        hub.dispatch_audio.assert_not_awaited()

    async def test_audio_and_text_dispatches_both(self) -> None:
        """Response with both content and audio: both dispatch methods called."""
        hub = Hub()
        msg = make_inbound_message(platform="telegram", bot_id="main")
        audio = OutboundAudio(audio_bytes=b"ogg_data", mime_type="audio/ogg")
        response = Response(content="hello", audio=audio)
        pipeline_result = _make_pipeline_result(response)

        hub.dispatch_audio = AsyncMock()
        hub.dispatch_response = AsyncMock()

        mock = AsyncMock(return_value=pipeline_result)
        with patch.object(MessagePipeline, "process", new=mock):
            await _run_hub_one_msg(hub, msg)

        hub.dispatch_response.assert_awaited_once_with(msg, response)
        hub.dispatch_audio.assert_awaited_once_with(msg, audio)

    async def test_empty_response_dispatches_nothing(self) -> None:
        """Empty Response (content="" audio=None): neither dispatch method called."""
        hub = Hub()
        msg = make_inbound_message(platform="telegram", bot_id="main")
        response = Response(content="", audio=None)
        pipeline_result = _make_pipeline_result(response)

        hub.dispatch_audio = AsyncMock()
        hub.dispatch_response = AsyncMock()

        mock = AsyncMock(return_value=pipeline_result)
        with patch.object(MessagePipeline, "process", new=mock):
            await _run_hub_one_msg(hub, msg)

        hub.dispatch_response.assert_not_awaited()
        hub.dispatch_audio.assert_not_awaited()
