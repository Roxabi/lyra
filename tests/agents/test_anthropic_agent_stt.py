"""Integration tests for AnthropicAgent AUDIO branch (T3.5).

Covers SC2–SC11 for AnthropicAgent:
  - AUDIO success: transcript prefixed for LLM, raw text in sdk_history
  - AUDIO empty transcript: retry prompt
  - AUDIO noise transcript: retry prompt
  - AUDIO transcription exception: error reply
  - AUDIO stt=None: unsupported reply
  - Temp file deleted in all paths (success, exception, no-STT)
  - Non-AUDIO text message unaffected (regression guard)
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.agent import Agent, ModelConfig
from lyra.core.message import (
    AudioContent,
    Message,
    MessageType,
    Platform,
    TelegramContext,
)
from lyra.core.pool import Pool
from lyra.stt import STTService, TranscriptionResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_audio_message(url: str) -> Message:
    return Message(
        id="msg-audio",
        platform=Platform.TELEGRAM,
        bot_id="main",
        user_id="alice",
        user_name="Alice",
        is_mention=False,
        is_from_bot=False,
        content=AudioContent(url=url),
        type=MessageType.AUDIO,
        timestamp=datetime.now(timezone.utc),
        platform_context=TelegramContext(chat_id=42),
    )


def make_text_message(text: str = "hello") -> Message:
    return Message(
        id="msg-text",
        platform=Platform.TELEGRAM,
        bot_id="main",
        user_id="alice",
        user_name="Alice",
        is_mention=False,
        is_from_bot=False,
        content=text,
        type=MessageType.TEXT,
        timestamp=datetime.now(timezone.utc),
        platform_context=TelegramContext(chat_id=42),
    )


def make_pool(pool_id: str = "telegram:main:alice") -> Pool:
    return Pool(pool_id=pool_id, agent_name="lyra", hub=MagicMock())


def make_config() -> Agent:
    return Agent(
        name="lyra",
        system_prompt="",
        memory_namespace="lyra",
        model_config=ModelConfig(
            backend="anthropic-sdk",
            model="claude-sonnet-4-5",
            max_turns=10,
        ),
    )


def make_mock_stt(
    result: TranscriptionResult | None = None, raises: Exception | None = None
) -> STTService:
    """Return a MagicMock standing in for STTService with transcribe pre-configured."""
    stt = MagicMock(spec=STTService)
    if raises is not None:
        stt.transcribe = AsyncMock(side_effect=raises)
    else:
        stt.transcribe = AsyncMock(return_value=result)
    return stt


def _make_mock_stream(text_chunks: list[str], stop_reason: str = "end_turn"):
    """Create a mock stream context manager that yields text_chunks."""
    mock_final = MagicMock()
    mock_final.content = [MagicMock(type="text", text="".join(text_chunks))]
    mock_final.stop_reason = stop_reason
    mock_final.usage.input_tokens = 100
    mock_final.usage.output_tokens = 50

    async def text_stream_iter():
        for chunk in text_chunks:
            yield chunk

    stream_ctx = MagicMock()
    stream_ctx.text_stream = text_stream_iter()
    stream_ctx.get_final_message = AsyncMock(return_value=mock_final)

    ctx_manager = AsyncMock()
    ctx_manager.__aenter__ = AsyncMock(return_value=stream_ctx)
    ctx_manager.__aexit__ = AsyncMock(return_value=False)

    return ctx_manager, mock_final


async def collect(agent, msg, pool) -> list[str]:
    """Drain an async generator agent.process() and return all chunks."""
    chunks = []
    async for chunk in agent.process(msg, pool):
        chunks.append(chunk)
    return chunks


# ---------------------------------------------------------------------------
# TestAnthropicAgentAudioBranch
# ---------------------------------------------------------------------------


class TestAnthropicAgentAudioBranch:
    async def test_audio_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AUDIO with valid transcript: LLM gets prefixed text; history stores raw."""
        # Arrange
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
        from lyra.agents.anthropic_agent import AnthropicAgent

        stt = make_mock_stt(TranscriptionResult("Hello world", "en", 1.5))
        agent = AnthropicAgent(make_config(), stt=stt)

        stream_ctx, _ = _make_mock_stream(["Sure, here is my reply."])
        agent._client.messages.stream = MagicMock(return_value=stream_ctx)

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            tmp_path = f.name

        msg = make_audio_message(tmp_path)
        pool = make_pool()

        # Act
        chunks = await collect(agent, msg, pool)

        # Assert — LLM received the prefixed text
        call_kwargs = agent._client.messages.stream.call_args.kwargs
        messages_sent = call_kwargs["messages"]
        user_message = next(m for m in messages_sent if m["role"] == "user")
        assert "🎤 [transcribed]: Hello world" in user_message["content"]

        # Assert — sdk_history stores raw text (no prefix)
        assert len(pool.sdk_history) >= 1
        user_history = next(m for m in pool.sdk_history if m["role"] == "user")
        assert user_history["content"] == "Hello world"
        assert "🎤" not in user_history["content"]

        # Assert — LLM reply was yielded
        assert chunks == ["Sure, here is my reply."]

    async def test_audio_empty_transcript(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AUDIO with empty transcript: agent replies with retry prompt, no LLM call."""
        # Arrange
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
        from lyra.agents.anthropic_agent import AnthropicAgent

        stt = make_mock_stt(TranscriptionResult("", "en", 0.5))
        agent = AnthropicAgent(make_config(), stt=stt)
        agent._client.messages.stream = MagicMock()

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            tmp_path = f.name

        msg = make_audio_message(tmp_path)
        pool = make_pool()

        # Act
        chunks = await collect(agent, msg, pool)

        # Assert
        full_reply = "".join(chunks)
        assert "couldn't make out" in full_reply.lower()
        agent._client.messages.stream.assert_not_called()

    async def test_audio_noise_transcript(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AUDIO with Whisper noise token: retry prompt returned."""
        # Arrange
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
        from lyra.agents.anthropic_agent import AnthropicAgent

        stt = make_mock_stt(TranscriptionResult("[Music]", "en", 0.5))
        agent = AnthropicAgent(make_config(), stt=stt)
        agent._client.messages.stream = MagicMock()

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            tmp_path = f.name

        msg = make_audio_message(tmp_path)
        pool = make_pool()

        # Act
        chunks = await collect(agent, msg, pool)

        # Assert
        full_reply = "".join(chunks)
        assert "couldn't make out" in full_reply.lower()
        agent._client.messages.stream.assert_not_called()

    async def test_audio_transcription_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AUDIO with transcription exception: error reply returned, no crash."""
        # Arrange
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
        from lyra.agents.anthropic_agent import AnthropicAgent

        stt = make_mock_stt(raises=RuntimeError("transcription failed"))
        agent = AnthropicAgent(make_config(), stt=stt)
        agent._client.messages.stream = MagicMock()

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            tmp_path = f.name

        msg = make_audio_message(tmp_path)
        pool = make_pool()

        # Act — RuntimeError should propagate (agent re-raises)
        with pytest.raises(RuntimeError, match="transcription failed"):
            await collect(agent, msg, pool)

    async def test_audio_no_stt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AUDIO with stt=None: unsupported reply returned."""
        # Arrange
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
        from lyra.agents.anthropic_agent import AnthropicAgent

        agent = AnthropicAgent(make_config(), stt=None)
        agent._client.messages.stream = MagicMock()

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            tmp_path = f.name

        msg = make_audio_message(tmp_path)
        pool = make_pool()

        # Act
        chunks = await collect(agent, msg, pool)

        # Assert
        full_reply = "".join(chunks)
        assert "not supported" in full_reply.lower()
        agent._client.messages.stream.assert_not_called()

    async def test_audio_tmp_file_deleted_on_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Temp file is unlinked after a successful transcription + LLM call."""
        # Arrange
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
        from lyra.agents.anthropic_agent import AnthropicAgent

        stt = make_mock_stt(TranscriptionResult("Hello world", "en", 1.5))
        agent = AnthropicAgent(make_config(), stt=stt)
        stream_ctx, _ = _make_mock_stream(["ok"])
        agent._client.messages.stream = MagicMock(return_value=stream_ctx)

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            tmp_path = f.name

        assert Path(tmp_path).exists()
        msg = make_audio_message(tmp_path)
        pool = make_pool()

        # Act
        await collect(agent, msg, pool)

        # Assert
        assert not Path(tmp_path).exists()

    async def test_audio_tmp_file_deleted_on_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Temp file is unlinked even when transcription raises an exception."""
        # Arrange
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
        from lyra.agents.anthropic_agent import AnthropicAgent

        stt = make_mock_stt(raises=RuntimeError("boom"))
        agent = AnthropicAgent(make_config(), stt=stt)

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            tmp_path = f.name

        assert Path(tmp_path).exists()
        msg = make_audio_message(tmp_path)
        pool = make_pool()

        # Act — exception propagates but file must still be deleted
        with pytest.raises(RuntimeError):
            await collect(agent, msg, pool)

        # Assert
        assert not Path(tmp_path).exists()

    async def test_audio_tmp_file_deleted_no_stt(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Temp file is unlinked even when stt=None (unsupported path)."""
        # Arrange
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
        from lyra.agents.anthropic_agent import AnthropicAgent

        agent = AnthropicAgent(make_config(), stt=None)

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            tmp_path = f.name

        assert Path(tmp_path).exists()
        msg = make_audio_message(tmp_path)
        pool = make_pool()

        # Act
        await collect(agent, msg, pool)

        # Assert
        assert not Path(tmp_path).exists()

    async def test_text_message_unaffected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Non-AUDIO messages follow the normal path — AUDIO branch not entered."""
        # Arrange
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
        from lyra.agents.anthropic_agent import AnthropicAgent

        stt = make_mock_stt(TranscriptionResult("should not be called", "en", 0.0))
        agent = AnthropicAgent(make_config(), stt=stt)
        stream_ctx, _ = _make_mock_stream(["text response"])
        agent._client.messages.stream = MagicMock(return_value=stream_ctx)

        msg = make_text_message("tell me something")
        pool = make_pool()

        # Act
        chunks = await collect(agent, msg, pool)

        # Assert — STT was never called
        cast(AsyncMock, stt.transcribe).assert_not_called()
        assert chunks == ["text response"]

        # Assert — sdk_history stores the literal user text
        user_history = next(m for m in pool.sdk_history if m["role"] == "user")
        assert user_history["content"] == "tell me something"
