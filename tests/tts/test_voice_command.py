"""Tests for /voice command handling in AnthropicAgent, SimpleAgent,
and Telegram adapter MIME routing (V4 of issue #167).
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lyra.agents.anthropic_agent import AnthropicAgent
from lyra.agents.simple_agent import SimpleAgent
from lyra.core.agent import Agent, ModelConfig
from lyra.core.auth import TrustLevel
from lyra.core.message import InboundMessage, OutboundAudio, Response
from lyra.core.pool import Pool
from lyra.core.runtime_config import RuntimeConfig
from lyra.tts import SynthesisResult

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_message(text: str = "hello") -> InboundMessage:
    return InboundMessage(
        id="msg-1",
        platform="telegram",
        bot_id="main",
        scope_id="chat:42",
        user_id="tg:user:alice",
        user_name="Alice",
        is_mention=False,
        text=text,
        text_raw=text,
        timestamp=datetime.now(timezone.utc),
        platform_meta={
            "chat_id": 42,
            "topic_id": None,
            "message_id": 99,
            "is_group": False,
        },
        trust_level=TrustLevel.TRUSTED,
    )


def _make_pool(pool_id: str = "telegram:main:alice") -> Pool:
    return Pool(pool_id=pool_id, agent_name="lyra", ctx=MagicMock())


def _make_agent_config() -> Agent:
    return Agent(
        name="lyra",
        system_prompt="You are Lyra.",
        memory_namespace="lyra",
        model_config=ModelConfig(
            backend="anthropic-sdk",
            model="claude-3-5-haiku-20241022",
        ),
    )


def _make_mock_provider() -> MagicMock:
    from lyra.llm.base import LlmResult

    provider = MagicMock()
    provider.capabilities = {"streaming": False, "auth": "api_key"}
    provider.complete = AsyncMock(return_value=LlmResult(result="llm reply"))
    return provider


def _make_synthesis_result() -> SynthesisResult:
    return SynthesisResult(
        audio_bytes=b"fake_wav_data",
        mime_type="audio/wav",
        duration_ms=500,
    )


def _make_tts_mock(result: SynthesisResult | None = None) -> AsyncMock:
    tts = AsyncMock()
    tts.synthesize = AsyncMock(return_value=result or _make_synthesis_result())
    return tts


# ---------------------------------------------------------------------------
# AnthropicAgent /voice pre-router
# ---------------------------------------------------------------------------


class TestAnthropicAgentVoiceCommand:
    """AnthropicAgent: /voice command intercepted before LLM dispatch."""

    def _make_agent(self, tts: object | None = None) -> AnthropicAgent:
        return AnthropicAgent(
            config=_make_agent_config(),
            provider=_make_mock_provider(),
            runtime_config=RuntimeConfig(),
            tts=tts,  # type: ignore[arg-type]
        )

    @pytest.mark.asyncio
    async def test_voice_command_returns_audio_response(self) -> None:
        """When tts is set and msg.text="/voice hello", process() returns
        Response with .audio set and .content == "".
        """
        # Arrange
        tts = _make_tts_mock()
        agent = self._make_agent(tts=tts)
        msg = _make_message("/voice hello")
        pool = _make_pool()

        # Act
        response = await agent.process(msg, pool)

        # Assert
        assert isinstance(response, Response)
        assert response.content == ""
        assert response.audio is not None
        assert isinstance(response.audio, OutboundAudio)
        assert response.audio.audio_bytes == b"fake_wav_data"
        assert response.audio.mime_type == "audio/wav"
        tts.synthesize.assert_awaited_once_with("hello")

    @pytest.mark.asyncio
    async def test_voice_command_text_fallback_on_tts_failure(self) -> None:
        """When tts.synthesize() raises RuntimeError, process() returns
        Response(content="Sorry, I couldn't generate audio.").
        """
        # Arrange
        tts = AsyncMock()
        tts.synthesize = AsyncMock(side_effect=RuntimeError("TTS failure"))
        agent = self._make_agent(tts=tts)
        msg = _make_message("/voice hello")
        pool = _make_pool()

        # Act
        response = await agent.process(msg, pool)

        # Assert
        assert isinstance(response, Response)
        assert response.content == "Sorry, I couldn't generate audio."
        assert response.audio is None

    @pytest.mark.asyncio
    async def test_non_voice_message_not_intercepted(self) -> None:
        """When msg.text="hello" (no /voice prefix), /voice branch is NOT taken;
        normal LLM processing returns its sentinel Response.
        """
        # Arrange
        tts = _make_tts_mock()
        agent = self._make_agent(tts=tts)
        msg = _make_message("hello")
        pool = _make_pool()
        sentinel = Response(content="sentinel_llm_reply")

        # Act
        with patch.object(agent, "_process_llm", return_value=sentinel) as mock_llm:
            response = await agent.process(msg, pool)

        # Assert
        mock_llm.assert_awaited_once()
        assert response.content == "sentinel_llm_reply"
        tts.synthesize.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_voice_command_bare_no_args_not_intercepted(self) -> None:
        """/voice with no trailing text (bare "/voice") falls through to LLM.

        The pre-router uses startswith("/voice ") — trailing space required.
        A bare "/voice" with no space+args does not match and is not intercepted.
        """
        tts = _make_tts_mock()
        agent = self._make_agent(tts=tts)
        msg = _make_message("/voice")
        pool = _make_pool()
        sentinel = Response(content="llm_handled_bare_voice")

        with patch.object(agent, "_process_llm", return_value=sentinel):
            response = await agent.process(msg, pool)

        tts.synthesize.assert_not_awaited()
        assert response.content == "llm_handled_bare_voice"

    @pytest.mark.asyncio
    async def test_voice_command_with_no_tts_not_intercepted(self) -> None:
        """When tts=None, msg.text="/voice hello" is passed through to
        normal LLM processing.
        """
        # Arrange
        agent = self._make_agent(tts=None)
        msg = _make_message("/voice hello")
        pool = _make_pool()
        sentinel = Response(content="llm_handled_voice")

        # Act
        with patch.object(agent, "_process_llm", return_value=sentinel) as mock_llm:
            response = await agent.process(msg, pool)

        # Assert
        mock_llm.assert_awaited_once()
        assert response.content == "llm_handled_voice"


# ---------------------------------------------------------------------------
# SimpleAgent /voice pre-router
# ---------------------------------------------------------------------------


class TestSimpleAgentVoiceCommand:
    """SimpleAgent: /voice command intercepted before provider dispatch."""

    def _make_agent(self, tts: object | None = None) -> SimpleAgent:
        return SimpleAgent(
            config=_make_agent_config(),
            provider=_make_mock_provider(),
            runtime_config=RuntimeConfig(),
            tts=tts,  # type: ignore[arg-type]
        )

    @pytest.mark.asyncio
    async def test_simple_agent_voice_command_returns_audio(self) -> None:
        """When tts is set and msg.text="/voice hello", process() returns
        Response with .audio set and .content == "".
        """
        # Arrange
        tts = _make_tts_mock()
        agent = self._make_agent(tts=tts)
        msg = _make_message("/voice hello")
        pool = _make_pool()

        # Act
        response = await agent.process(msg, pool)

        # Assert
        assert isinstance(response, Response)
        assert response.content == ""
        assert response.audio is not None
        assert isinstance(response.audio, OutboundAudio)
        assert response.audio.audio_bytes == b"fake_wav_data"
        assert response.audio.mime_type == "audio/wav"
        tts.synthesize.assert_awaited_once_with("hello")

    @pytest.mark.asyncio
    async def test_simple_agent_voice_bare_no_args_not_intercepted(self) -> None:
        """/voice with no trailing text falls through to provider (not intercepted)."""
        tts = _make_tts_mock()
        agent = self._make_agent(tts=tts)
        msg = _make_message("/voice")
        pool = _make_pool()
        result = await agent.process(msg, pool)

        tts.synthesize.assert_not_awaited()
        # Falls through to provider (which returns LLM response for "/voice" text)
        assert result.audio is None

    @pytest.mark.asyncio
    async def test_simple_agent_voice_fallback_on_failure(self) -> None:
        """When tts.synthesize() raises RuntimeError, process() returns
        Response(content="Sorry, I couldn't generate audio.").
        """
        # Arrange
        tts = AsyncMock()
        tts.synthesize = AsyncMock(side_effect=RuntimeError("TTS backend error"))
        agent = self._make_agent(tts=tts)
        msg = _make_message("/voice hello")
        pool = _make_pool()

        # Act
        response = await agent.process(msg, pool)

        # Assert
        assert isinstance(response, Response)
        assert response.content == "Sorry, I couldn't generate audio."
        assert response.audio is None


# ---------------------------------------------------------------------------
# TelegramAdapter render_audio() MIME routing
# ---------------------------------------------------------------------------


class TestTelegramAdapterRenderAudio:
    """TelegramAdapter.render_audio() routes to send_audio vs send_voice
    based on MIME type.
    """

    def _make_adapter(self) -> object:
        from lyra.adapters.telegram import _ALLOW_ALL, TelegramAdapter

        hub = MagicMock()
        adapter = TelegramAdapter(
            bot_id="main",
            token="test-token",
            hub=hub,
            auth=_ALLOW_ALL,
        )
        adapter.bot = AsyncMock()
        return adapter

    def _make_inbound(self) -> InboundMessage:
        return _make_message()

    @pytest.mark.asyncio
    async def test_render_audio_wav_uses_send_audio(self) -> None:
        """OutboundAudio(mime_type="audio/wav") calls send_audio(), not send_voice()."""
        # Arrange
        from lyra.adapters.telegram import TelegramAdapter

        adapter: TelegramAdapter = self._make_adapter()  # type: ignore[assignment]
        inbound = self._make_inbound()
        audio_msg = OutboundAudio(
            audio_bytes=b"wav_data",
            mime_type="audio/wav",
            duration_ms=500,
        )

        # Act
        await adapter.render_audio(audio_msg, inbound)

        # Assert
        adapter.bot.send_audio.assert_awaited_once()  # type: ignore[attr-defined]
        adapter.bot.send_voice.assert_not_awaited()  # type: ignore[attr-defined]
        call_kwargs = adapter.bot.send_audio.call_args.kwargs  # type: ignore[attr-defined]
        assert call_kwargs["chat_id"] == 42

    @pytest.mark.asyncio
    async def test_render_audio_ogg_uses_send_voice(self) -> None:
        """OutboundAudio(mime_type="audio/ogg") calls send_voice(), not send_audio()."""
        # Arrange
        from lyra.adapters.telegram import TelegramAdapter

        adapter: TelegramAdapter = self._make_adapter()  # type: ignore[assignment]
        inbound = self._make_inbound()
        audio_msg = OutboundAudio(
            audio_bytes=b"ogg_data",
            mime_type="audio/ogg",
            duration_ms=300,
        )

        # Act
        await adapter.render_audio(audio_msg, inbound)

        # Assert
        adapter.bot.send_voice.assert_awaited_once()  # type: ignore[attr-defined]
        adapter.bot.send_audio.assert_not_awaited()  # type: ignore[attr-defined]
        call_kwargs = adapter.bot.send_voice.call_args.kwargs  # type: ignore[attr-defined]
        assert call_kwargs["chat_id"] == 42

    @pytest.mark.asyncio
    async def test_render_audio_mpeg_uses_send_audio(self) -> None:
        """OutboundAudio(mime_type="audio/mpeg") calls send_audio(), not send_voice().
        """
        # Arrange
        from lyra.adapters.telegram import TelegramAdapter

        adapter: TelegramAdapter = self._make_adapter()  # type: ignore[assignment]
        inbound = self._make_inbound()
        audio_msg = OutboundAudio(
            audio_bytes=b"mp3_data",
            mime_type="audio/mpeg",
            duration_ms=1200,
        )

        # Act
        await adapter.render_audio(audio_msg, inbound)

        # Assert
        adapter.bot.send_audio.assert_awaited_once()  # type: ignore[attr-defined]
        adapter.bot.send_voice.assert_not_awaited()  # type: ignore[attr-defined]
        call_kwargs = adapter.bot.send_audio.call_args.kwargs  # type: ignore[attr-defined]
        assert call_kwargs["chat_id"] == 42
