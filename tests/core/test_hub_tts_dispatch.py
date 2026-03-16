"""Tests for Hub.run() audio dispatch via Response.audio (#167 V2).

Verifies that Hub.run() correctly dispatches audio from Response.audio
when the pipeline returns a COMMAND_HANDLED result containing a Response
with an audio field set.
"""

from __future__ import annotations

import asyncio
import dataclasses
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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

    async def test_audio_dispatch_failure_does_not_block_text(self) -> None:
        """Audio dispatch failure must not suppress the text reply.

        When dispatch_audio() raises, dispatch_response() for content
        must still be called (independent error handling after F2 fix).
        """
        hub = Hub()
        msg = make_inbound_message(platform="telegram", bot_id="main")
        audio = OutboundAudio(audio_bytes=b"ogg_data", mime_type="audio/ogg")
        response = Response(content="hello", audio=audio)
        pipeline_result = _make_pipeline_result(response)

        hub.dispatch_audio = AsyncMock(side_effect=RuntimeError("send_voice failed"))
        hub.dispatch_response = AsyncMock()

        mock = AsyncMock(return_value=pipeline_result)
        with patch.object(MessagePipeline, "process", new=mock):
            await _run_hub_one_msg(hub, msg)

        hub.dispatch_response.assert_awaited_once_with(msg, response)


# ---------------------------------------------------------------------------
# S4 — T18: Hub accepts prefs_store=None
# S5 — T22-T25: Pref resolution in _synthesize_and_dispatch_audio
# ---------------------------------------------------------------------------


def _make_msg_with_language(language: str | None) -> InboundMessage:
    """Build an InboundMessage with a specific language field."""
    base = make_inbound_message(platform="telegram", bot_id="main", user_id="tg:user:1")
    return dataclasses.replace(base, language=language)


def _make_mock_prefs_store(tts_language: str, tts_voice: str = "agent_default"):
    """Return a mock PrefsStore that returns fixed UserPrefs."""
    mock_store = MagicMock()

    class _FakePrefs:
        def __init__(self):
            self.tts_language = tts_language
            self.tts_voice = tts_voice

    mock_store.get_prefs = AsyncMock(return_value=_FakePrefs())
    return mock_store


class TestHubPrefsStoreInit:
    """T18 — Hub.__init__ must accept prefs_store keyword argument."""

    def test_hub_accepts_prefs_store_none(self):
        """Hub(prefs_store=None) must not raise TypeError."""
        hub = Hub(prefs_store=None)
        assert hub._prefs_store is None

    def test_hub_stores_prefs_store_when_provided(self):
        """Hub stores the injected prefs_store on self._prefs_store."""
        mock_store = MagicMock()
        hub = Hub(prefs_store=mock_store)
        assert hub._prefs_store is mock_store


class TestHubPrefResolution:
    """T22-T25 — _synthesize_and_dispatch_audio resolves language from prefs."""

    @pytest.mark.asyncio
    async def test_explicit_pref_overrides_detected_language(self):
        """Explicit tts_language='en' beats msg.language='fr'."""
        mock_tts = MagicMock()
        mock_tts.synthesize = AsyncMock(
            return_value=MagicMock(
                audio_bytes=b"data", mime_type="audio/ogg", duration_ms=None
            )
        )
        prefs_store = _make_mock_prefs_store(tts_language="en")
        hub = Hub(tts=mock_tts, prefs_store=prefs_store)
        hub.dispatch_audio = AsyncMock()

        msg = _make_msg_with_language("fr")
        await hub._audio_pipeline._synthesize_and_dispatch_audio(msg, "reply")

        mock_tts.synthesize.assert_awaited_once()
        call_kwargs = mock_tts.synthesize.call_args.kwargs
        assert call_kwargs.get("language") == "en"

    @pytest.mark.asyncio
    async def test_detected_mode_uses_msg_language(self):
        """tts_language='detected' + msg.language='fr' → language='fr' forwarded."""
        mock_tts = MagicMock()
        mock_tts.synthesize = AsyncMock(
            return_value=MagicMock(
                audio_bytes=b"data", mime_type="audio/ogg", duration_ms=None
            )
        )
        prefs_store = _make_mock_prefs_store(tts_language="detected")
        hub = Hub(tts=mock_tts, prefs_store=prefs_store)
        hub.dispatch_audio = AsyncMock()

        msg = _make_msg_with_language("fr")
        await hub._audio_pipeline._synthesize_and_dispatch_audio(msg, "reply")

        mock_tts.synthesize.assert_awaited_once()
        call_kwargs = mock_tts.synthesize.call_args.kwargs
        assert call_kwargs.get("language") == "fr"

    @pytest.mark.asyncio
    async def test_none_language_falls_through(self):
        """tts_language='detected' + msg.language=None → language=None forwarded."""
        mock_tts = MagicMock()
        mock_tts.synthesize = AsyncMock(
            return_value=MagicMock(
                audio_bytes=b"data", mime_type="audio/ogg", duration_ms=None
            )
        )
        prefs_store = _make_mock_prefs_store(tts_language="detected")
        hub = Hub(tts=mock_tts, prefs_store=prefs_store)
        hub.dispatch_audio = AsyncMock()

        msg = _make_msg_with_language(None)
        await hub._audio_pipeline._synthesize_and_dispatch_audio(msg, "reply")

        mock_tts.synthesize.assert_awaited_once()
        call_kwargs = mock_tts.synthesize.call_args.kwargs
        assert call_kwargs.get("language") is None

    @pytest.mark.asyncio
    async def test_sentinel_detected_not_forwarded(self):
        """The sentinel 'detected' must never reach synthesize() as a language value."""
        mock_tts = MagicMock()
        mock_tts.synthesize = AsyncMock(
            return_value=MagicMock(
                audio_bytes=b"data", mime_type="audio/ogg", duration_ms=None
            )
        )
        prefs_store = _make_mock_prefs_store(tts_language="detected")
        hub = Hub(tts=mock_tts, prefs_store=prefs_store)
        hub.dispatch_audio = AsyncMock()

        msg = _make_msg_with_language("fr")
        await hub._audio_pipeline._synthesize_and_dispatch_audio(msg, "reply")

        mock_tts.synthesize.assert_awaited_once()
        call_kwargs = mock_tts.synthesize.call_args.kwargs
        assert call_kwargs.get("language") != "detected"
        assert call_kwargs.get("voice") != "agent_default"


class TestHubPrefResolutionNonePrefsStore:
    """SC-4 — Hub with prefs_store=None falls back to msg.language."""

    @pytest.mark.asyncio
    async def test_none_prefs_store_uses_msg_language(self):
        """prefs_store=None + msg.language='fr' → synthesize with language='fr'."""
        mock_tts = MagicMock()
        mock_tts.synthesize = AsyncMock(
            return_value=MagicMock(
                audio_bytes=b"data", mime_type="audio/ogg", duration_ms=None
            )
        )
        hub = Hub(tts=mock_tts, prefs_store=None)
        hub.dispatch_audio = AsyncMock()

        base = make_inbound_message(
            platform="telegram", bot_id="main", user_id="tg:user:1"
        )
        msg = dataclasses.replace(base, language="fr")
        await hub._audio_pipeline._synthesize_and_dispatch_audio(msg, "reply")

        mock_tts.synthesize.assert_awaited_once()
        call_kwargs = mock_tts.synthesize.call_args.kwargs
        assert call_kwargs.get("language") == "fr"

    @pytest.mark.asyncio
    async def test_none_prefs_store_with_none_language(self):
        """prefs_store=None + msg.language=None → synthesize with language=None."""
        mock_tts = MagicMock()
        mock_tts.synthesize = AsyncMock(
            return_value=MagicMock(
                audio_bytes=b"data", mime_type="audio/ogg", duration_ms=None
            )
        )
        hub = Hub(tts=mock_tts, prefs_store=None)
        hub.dispatch_audio = AsyncMock()

        base = make_inbound_message(
            platform="telegram", bot_id="main", user_id="tg:user:1"
        )
        msg = dataclasses.replace(base, language=None)
        await hub._audio_pipeline._synthesize_and_dispatch_audio(msg, "reply")

        mock_tts.synthesize.assert_awaited_once()
        call_kwargs = mock_tts.synthesize.call_args.kwargs
        assert call_kwargs.get("language") is None
