"""Tests for AudioPipeline per-agent TTS wiring (#280).

Covers: synthesize_and_dispatch_audio agent_tts forwarding,
_resolve_agent_tts binding resolution, dispatch_response e2e TTS call.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.agent_config import AgentTTSConfig
from lyra.core.hub import Hub
from lyra.core.message import InboundMessage, Platform, Response
from lyra.core.trust import TrustLevel
from tests.core.conftest import FakeSTT

# ---------------------------------------------------------------------------
# T2: synthesize_and_dispatch_audio forwards agent_tts
# ---------------------------------------------------------------------------


class TestSynthesizeDispatchAgentTTS:
    """synthesize_and_dispatch_audio forwards agent_tts to synthesize()."""

    @pytest.mark.asyncio()
    async def test_agent_tts_forwarded_to_synthesize(self):
        """When agent_tts is passed, it reaches TTSService.synthesize()."""
        from lyra.tts import SynthesisResult

        agent_tts = AgentTTSConfig(engine="agent_eng", voice="agent_vox")

        mock_tts = MagicMock()
        mock_tts.synthesize = AsyncMock(
            return_value=SynthesisResult(
                audio_bytes=b"fake",
                mime_type="audio/ogg",
                duration_ms=100,
            )
        )

        hub = Hub(stt=FakeSTT())  # type: ignore[arg-type]
        hub._tts = mock_tts
        hub.dispatch_audio = AsyncMock()  # type: ignore[method-assign]

        msg = InboundMessage(
            id="msg-tts-1",
            platform="telegram",
            bot_id="main",
            scope_id="chat:42",
            user_id="alice",
            user_name="Alice",
            is_mention=False,
            text="hello",
            text_raw="hello",
            timestamp=datetime.now(timezone.utc),
            trust_level=TrustLevel.TRUSTED,
        )

        await hub._audio_pipeline.synthesize_and_dispatch_audio(
            msg, "Hello world", agent_tts=agent_tts
        )

        mock_tts.synthesize.assert_awaited_once()
        call_kwargs = mock_tts.synthesize.call_args
        assert call_kwargs.kwargs.get("agent_tts") is agent_tts

    @pytest.mark.asyncio()
    async def test_agent_tts_none_no_regression(self):
        """Without agent_tts, synthesize() is called without it."""
        from lyra.tts import SynthesisResult

        mock_tts = MagicMock()
        mock_tts.synthesize = AsyncMock(
            return_value=SynthesisResult(
                audio_bytes=b"fake",
                mime_type="audio/ogg",
                duration_ms=100,
            )
        )

        hub = Hub(stt=FakeSTT())  # type: ignore[arg-type]
        hub._tts = mock_tts
        hub.dispatch_audio = AsyncMock()  # type: ignore[method-assign]

        msg = InboundMessage(
            id="msg-tts-2",
            platform="telegram",
            bot_id="main",
            scope_id="chat:42",
            user_id="bob",
            user_name="Bob",
            is_mention=False,
            text="hello",
            text_raw="hello",
            timestamp=datetime.now(timezone.utc),
            trust_level=TrustLevel.TRUSTED,
        )

        await hub._audio_pipeline.synthesize_and_dispatch_audio(msg, "Hello world")

        mock_tts.synthesize.assert_awaited_once()
        call_kwargs = mock_tts.synthesize.call_args
        assert call_kwargs.kwargs.get("agent_tts") is None


# ---------------------------------------------------------------------------
# T4: _resolve_agent_tts resolves from agent registry via binding
# ---------------------------------------------------------------------------


class TestResolveAgentTTS:
    """T4 — hub._resolve_agent_tts resolves per-agent TTS config via binding."""

    def test_resolve_agent_tts_returns_config_from_registry(self):
        """_resolve_agent_tts returns the agent's tts config for a bound message."""
        from lyra.core import Agent
        from lyra.core.agent import AgentBase
        from lyra.core.agent_config import AgentTTSConfig

        # Arrange — build a concrete AgentBase subclass
        class FakeAgent(AgentBase):
            async def process(self, msg, pool, *, on_intermediate=None):  # type: ignore[override]
                pass

        agent_tts = AgentTTSConfig(engine="agent_eng", voice="agent_vox")
        cfg = Agent(
            name="test-agent",
            system_prompt="",
            memory_namespace="test-agent",
            tts=agent_tts,
        )
        fake_agent = FakeAgent(cfg)

        hub = Hub(stt=FakeSTT())  # type: ignore[arg-type]
        hub.agent_registry["test-agent"] = fake_agent

        # Register a binding: platform=telegram, bot=main, scope=chat:42 → test-agent
        hub.register_binding(
            platform=Platform.TELEGRAM,
            bot_id="main",
            scope_id="chat:42",
            agent_name="test-agent",
            pool_id="telegram:main:chat:42",
        )

        # Create a message that matches the binding
        msg = InboundMessage(
            id="msg-resolve-1",
            platform="telegram",
            bot_id="main",
            scope_id="chat:42",
            user_id="alice",
            user_name="Alice",
            is_mention=False,
            text="hello",
            text_raw="hello",
            timestamp=datetime.now(timezone.utc),
            trust_level=TrustLevel.TRUSTED,
        )

        # Act
        resolved = hub._resolve_agent_tts(msg)

        # Assert
        assert resolved is agent_tts
        assert resolved is not None
        assert resolved.engine == "agent_eng"
        assert resolved.voice == "agent_vox"

    def test_resolve_agent_tts_returns_none_without_binding(self):
        """_resolve_agent_tts returns None when no binding matches the message."""
        hub = Hub(stt=FakeSTT())  # type: ignore[arg-type]

        msg = InboundMessage(
            id="msg-resolve-2",
            platform="telegram",
            bot_id="main",
            scope_id="chat:99",
            user_id="bob",
            user_name="Bob",
            is_mention=False,
            text="hello",
            text_raw="hello",
            timestamp=datetime.now(timezone.utc),
            trust_level=TrustLevel.TRUSTED,
        )

        # Act
        resolved = hub._resolve_agent_tts(msg)

        # Assert — no binding registered, must return None
        assert resolved is None


# ---------------------------------------------------------------------------
# T7: dispatch_response → _resolve_agent_tts → synthesize (e2e integration)
# ---------------------------------------------------------------------------


class TestDispatchResponseAgentTTSE2E:
    """T7 — dispatch_response calls synthesize with the resolved agent_tts."""

    @pytest.mark.asyncio()
    async def test_dispatch_response_voice_calls_synthesize_with_agent_tts(self):
        """Voice-modality dispatch_response synthesizes audio with agent_tts."""
        from lyra.core import Agent
        from lyra.core.agent import AgentBase
        from lyra.core.agent_config import AgentTTSConfig
        from lyra.core.hub_protocol import ChannelAdapter
        from lyra.tts import SynthesisResult

        # Arrange — concrete agent with custom TTS
        class FakeAgent(AgentBase):
            async def process(self, msg, pool, *, on_intermediate=None):  # type: ignore[override]
                pass

        agent_tts = AgentTTSConfig(engine="e2e_eng", voice="e2e_vox")
        cfg = Agent(
            name="e2e-agent",
            system_prompt="",
            memory_namespace="e2e-agent",
            tts=agent_tts,
        )
        fake_agent = FakeAgent(cfg)

        # Build hub with mock TTS
        hub = Hub(stt=FakeSTT())  # type: ignore[arg-type]
        mock_tts = MagicMock()
        mock_tts.synthesize = AsyncMock(
            return_value=SynthesisResult(
                audio_bytes=b"audio",
                mime_type="audio/ogg",
                duration_ms=100,
            )
        )
        hub._tts = mock_tts

        hub.agent_registry["e2e-agent"] = fake_agent
        hub.register_binding(
            platform=Platform.TELEGRAM,
            bot_id="bot-e2e",
            scope_id="chat:1",
            agent_name="e2e-agent",
            pool_id="telegram:bot-e2e:chat:1",
        )

        # Register a fake adapter so dispatch_response doesn't raise KeyError
        fake_adapter = MagicMock(spec=ChannelAdapter)
        fake_adapter.send = AsyncMock()
        hub.adapter_registry[(Platform.TELEGRAM, "bot-e2e")] = fake_adapter

        # Voice-modality message
        msg = InboundMessage(
            id="msg-e2e-1",
            platform="telegram",
            bot_id="bot-e2e",
            scope_id="chat:1",
            user_id="alice",
            user_name="Alice",
            is_mention=False,
            text="hello",
            text_raw="hello",
            timestamp=datetime.now(timezone.utc),
            trust_level=TrustLevel.TRUSTED,
            modality="voice",
        )

        # Act — dispatch_response triggers TTS synthesis for voice modality
        await hub.dispatch_response(msg, Response(content="Hi there"))

        # Wait briefly for the background TTS task to complete
        await asyncio.sleep(0.1)

        # Assert — synthesize was called with the agent's tts config
        mock_tts.synthesize.assert_awaited()
        call_kwargs = mock_tts.synthesize.call_args
        assert call_kwargs.kwargs.get("agent_tts") is agent_tts
