"""Tests for /voice command handling in AnthropicAgent, SimpleAgent,
and Telegram adapter MIME routing (V4 of issue #167).

/voice <prompt> rewrites the message as a voice-modality LLM request:
- strips the /voice prefix
- injects a spoken-language hint
- sets modality="voice"
- falls through to the normal LLM pipeline (TTS is done by hub.dispatch_response)
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


def _make_tts_mock() -> AsyncMock:
    tts = AsyncMock()
    tts.synthesize = AsyncMock()
    return tts


# ---------------------------------------------------------------------------
# _handle_voice_command unit tests
# ---------------------------------------------------------------------------


class TestHandleVoiceCommand:
    """AgentBase._handle_voice_command() rewrites /voice messages."""

    def _make_agent(self, tts: object | None = None) -> AnthropicAgent:
        return AnthropicAgent(
            config=_make_agent_config(),
            provider=_make_mock_provider(),
            runtime_config=RuntimeConfig(),
            tts=tts,  # type: ignore[arg-type]
        )

    def test_returns_rewritten_message_with_voice_modality(self) -> None:
        """/voice hello → message rewritten with modality="voice" and hint prepended."""
        agent = self._make_agent(tts=_make_tts_mock())
        msg = _make_message("/voice hello")

        result = agent._handle_voice_command(msg)

        assert result is not None
        assert result.modality == "voice"
        assert result.text_raw == "hello"
        assert "hello" in result.text
        assert "[Voice" in result.text

    def test_strips_prefix_correctly(self) -> None:
        """The raw prompt (without hint) is preserved in text_raw."""
        agent = self._make_agent(tts=_make_tts_mock())
        msg = _make_message("/voice What is the weather today?")

        result = agent._handle_voice_command(msg)

        assert result is not None
        assert result.text_raw == "What is the weather today?"

    def test_returns_none_for_non_voice_message(self) -> None:
        """Regular messages return None (no rewrite)."""
        agent = self._make_agent(tts=_make_tts_mock())
        assert agent._handle_voice_command(_make_message("hello")) is None

    def test_returns_none_when_tts_not_configured(self) -> None:
        """When tts=None, /voice messages are not intercepted."""
        agent = self._make_agent(tts=None)
        assert agent._handle_voice_command(_make_message("/voice hello")) is None

    def test_returns_none_for_bare_voice_no_args(self) -> None:
        """Bare '/voice' with no prompt returns None (falls through to LLM as-is)."""
        agent = self._make_agent(tts=_make_tts_mock())
        assert agent._handle_voice_command(_make_message("/voice")) is None


# ---------------------------------------------------------------------------
# AnthropicAgent.process() integration
# ---------------------------------------------------------------------------


class TestAnthropicAgentVoiceCommand:
    """AnthropicAgent: /voice rewrites message and passes to LLM pipeline."""

    def _make_agent(self, tts: object | None = None) -> AnthropicAgent:
        return AnthropicAgent(
            config=_make_agent_config(),
            provider=_make_mock_provider(),
            runtime_config=RuntimeConfig(),
            tts=tts,  # type: ignore[arg-type]
        )

    @pytest.mark.asyncio
    async def test_voice_command_passes_to_llm_with_voice_modality(self) -> None:
        """/voice hello → LLM is called with modality="voice", no direct TTS."""
        tts = _make_tts_mock()
        agent = self._make_agent(tts=tts)
        msg = _make_message("/voice hello")
        pool = _make_pool()
        sentinel = Response(content="llm voice reply")

        with patch.object(agent, "_process_llm", return_value=sentinel) as mock_llm:
            response = await agent.process(msg, pool)

        mock_llm.assert_awaited_once()
        called_msg = mock_llm.call_args[0][0]
        assert called_msg.modality == "voice"
        assert called_msg.text_raw == "hello"
        assert response.content == "llm voice reply"
        tts.synthesize.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_non_voice_message_not_intercepted(self) -> None:
        """Regular messages are passed to LLM unmodified."""
        tts = _make_tts_mock()
        agent = self._make_agent(tts=tts)
        msg = _make_message("hello")
        pool = _make_pool()
        sentinel = Response(content="sentinel_llm_reply")

        with patch.object(agent, "_process_llm", return_value=sentinel) as mock_llm:
            response = await agent.process(msg, pool)

        called_msg = mock_llm.call_args[0][0]
        assert called_msg.modality is None
        assert response.content == "sentinel_llm_reply"
        tts.synthesize.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_voice_command_bare_no_args_falls_through_unmodified(self) -> None:
        """Bare '/voice' with no args goes to LLM as-is (modality unchanged)."""
        tts = _make_tts_mock()
        agent = self._make_agent(tts=tts)
        msg = _make_message("/voice")
        pool = _make_pool()
        sentinel = Response(content="llm_handled_bare_voice")

        with patch.object(agent, "_process_llm", return_value=sentinel) as mock_llm:
            response = await agent.process(msg, pool)

        called_msg = mock_llm.call_args[0][0]
        assert called_msg.modality is None
        tts.synthesize.assert_not_awaited()
        assert response.content == "llm_handled_bare_voice"

    @pytest.mark.asyncio
    async def test_voice_command_with_no_tts_falls_through_unmodified(self) -> None:
        """When tts=None, /voice hello is passed to LLM unmodified (no rewrite)."""
        agent = self._make_agent(tts=None)
        msg = _make_message("/voice hello")
        pool = _make_pool()
        sentinel = Response(content="llm_handled_voice")

        with patch.object(agent, "_process_llm", return_value=sentinel) as mock_llm:
            response = await agent.process(msg, pool)

        called_msg = mock_llm.call_args[0][0]
        assert called_msg.modality is None
        assert called_msg.text == "/voice hello"
        assert response.content == "llm_handled_voice"


# ---------------------------------------------------------------------------
# SimpleAgent /voice pre-router
# ---------------------------------------------------------------------------


class TestSimpleAgentVoiceCommand:
    """SimpleAgent: /voice rewrites message as voice-modality LLM request."""

    def _make_agent(self, tts: object | None = None) -> SimpleAgent:
        return SimpleAgent(
            config=_make_agent_config(),
            provider=_make_mock_provider(),
            runtime_config=RuntimeConfig(),
            tts=tts,  # type: ignore[arg-type]
        )

    @pytest.mark.asyncio
    async def test_simple_agent_voice_command_passes_to_llm(self) -> None:
        """SimpleAgent: /voice hello → LLM processes it with modality="voice"."""
        tts = _make_tts_mock()
        agent = self._make_agent(tts=tts)
        msg = _make_message("/voice hello")
        pool = _make_pool()

        # SimpleAgent calls provider directly; LLM returns "llm reply" (from mock)
        response = await agent.process(msg, pool)

        # TTS is NOT called directly by the pre-router
        tts.synthesize.assert_not_awaited()
        # LLM text response is returned (hub will TTS it via modality="voice")
        assert isinstance(response, Response)
        assert response.audio is None

    @pytest.mark.asyncio
    async def test_simple_agent_voice_bare_no_args_not_intercepted(self) -> None:
        """/voice with no trailing text falls through to provider unmodified."""
        tts = _make_tts_mock()
        agent = self._make_agent(tts=tts)
        msg = _make_message("/voice")
        pool = _make_pool()
        result = await agent.process(msg, pool)

        tts.synthesize.assert_not_awaited()
        assert result.audio is None


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
        """OutboundAudio(mime_type="audio/mpeg") calls send_audio(), not send_voice()."""  # noqa: E501
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
