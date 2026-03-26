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

from lyra.core.agent import Agent
from lyra.core.agent_config import ModelConfig
from lyra.core.message import Attachment, InboundMessage, Response
from lyra.core.pool import Pool
from lyra.core.trust import TrustLevel
from lyra.llm.base import LlmResult
from lyra.stt import STTService, TranscriptionResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_audio_message(url: str) -> InboundMessage:
    return InboundMessage(
        id="msg-audio",
        platform="telegram",
        bot_id="main",
        scope_id="chat:42",
        user_id="tg:user:alice",
        user_name="Alice",
        is_mention=False,
        text="",
        text_raw="",
        attachments=[
            Attachment(type="audio", url_or_path_or_bytes=url, mime_type="audio/ogg"),
        ],
        timestamp=datetime.now(timezone.utc),
        platform_meta={
            "chat_id": 42,
            "topic_id": None,
            "is_group": False,
            "message_id": None,
        },
        trust_level=TrustLevel.TRUSTED,
    )


def make_text_message(text: str = "hello") -> InboundMessage:
    return InboundMessage(
        id="msg-text",
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
            "is_group": False,
            "message_id": None,
        },
        trust_level=TrustLevel.TRUSTED,
    )


def make_pool(pool_id: str = "telegram:main:alice") -> Pool:
    return Pool(pool_id=pool_id, agent_name="lyra", ctx=MagicMock())


def make_config() -> Agent:
    return Agent(
        name="lyra",
        system_prompt="",
        memory_namespace="lyra",
        llm_config=ModelConfig(
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


def make_mock_provider(reply: str = "ok") -> MagicMock:
    """Return a mock LlmProvider that returns a successful LlmResult."""
    provider = MagicMock()
    provider.capabilities = {"streaming": False, "auth": "api_key"}
    provider.complete = AsyncMock(return_value=LlmResult(result=reply))
    return provider


async def process(agent, msg, pool) -> Response:
    """Call agent.process() and return the Response."""
    return await agent.process(msg, pool)


# ---------------------------------------------------------------------------
# TestAnthropicAgentAudioBranch
# ---------------------------------------------------------------------------


class TestAnthropicAgentAudioBranch:
    async def test_audio_success(self) -> None:
        """AUDIO with valid transcript: LLM gets prefixed text; history stores raw."""
        # Arrange
        from lyra.agents.anthropic_agent import AnthropicAgent

        stt = make_mock_stt(TranscriptionResult("Hello world", "en", 1.5))
        provider = make_mock_provider("Sure, here is my reply.")
        agent = AnthropicAgent(make_config(), provider, stt=stt)

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            tmp_path = f.name

        msg = make_audio_message(tmp_path)
        pool = make_pool()

        # Act
        response = await process(agent, msg, pool)

        # Assert — LLM received clean text (no emoji prefix)
        call_kwargs = provider.complete.call_args
        messages_sent = call_kwargs.kwargs["messages"]
        user_message = next(m for m in messages_sent if m["role"] == "user")
        assert user_message["content"] == "Hello world"
        assert "\U0001f3a4" not in user_message["content"]

        # Assert — sdk_history stores same clean text
        assert len(pool.sdk_history) >= 1
        user_history = next(m for m in pool.sdk_history if m["role"] == "user")
        assert user_history["content"] == "Hello world"
        assert "\U0001f3a4" not in user_history["content"]

        # Assert — LLM reply returned
        assert response.content == "Sure, here is my reply."

    async def test_audio_empty_transcript(self) -> None:
        """AUDIO with empty transcript: agent replies with retry prompt, no LLM call."""
        # Arrange
        from lyra.agents.anthropic_agent import AnthropicAgent

        stt = make_mock_stt(TranscriptionResult("", "en", 0.5))
        provider = make_mock_provider()
        agent = AnthropicAgent(make_config(), provider, stt=stt)

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            tmp_path = f.name

        msg = make_audio_message(tmp_path)
        pool = make_pool()

        # Act
        response = await process(agent, msg, pool)

        # Assert
        assert "couldn't make out" in response.content.lower()
        provider.complete.assert_not_called()

    @pytest.mark.parametrize(
        "noise_text",
        [
            "[Music]",
            "[Applause]",
            "[Laughter]",
            "[Silence]",
            "[Noise]",
            "   ",  # whitespace-only
        ],
    )
    async def test_audio_noise_transcript(self, noise_text: str) -> None:
        """SC5: noise/whitespace-only transcripts trigger retry prompt."""
        # Arrange
        from lyra.agents.anthropic_agent import AnthropicAgent

        stt = make_mock_stt(TranscriptionResult(noise_text, "en", 0.5))
        provider = make_mock_provider()
        agent = AnthropicAgent(make_config(), provider, stt=stt)

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            tmp_path = f.name

        msg = make_audio_message(tmp_path)
        pool = make_pool()

        # Act
        response = await process(agent, msg, pool)

        # Assert
        assert "couldn't make out" in response.content.lower()
        provider.complete.assert_not_called()

    async def test_audio_transcription_exception(self) -> None:
        """SC6: transcription exception propagates to caller (AnthropicAgent re-raises
        for circuit breaker). Contrast: SimpleAgent catches and returns error Response.
        """
        # Arrange
        from lyra.agents.anthropic_agent import AnthropicAgent

        stt = make_mock_stt(raises=RuntimeError("transcription failed"))
        provider = make_mock_provider()
        agent = AnthropicAgent(make_config(), provider, stt=stt)

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            tmp_path = f.name

        msg = make_audio_message(tmp_path)
        pool = make_pool()

        # Act — RuntimeError should propagate (agent re-raises)
        with pytest.raises(RuntimeError, match="transcription failed"):
            await process(agent, msg, pool)

    async def test_audio_no_stt(self) -> None:
        """AUDIO with stt=None: unsupported reply returned."""
        # Arrange
        from lyra.agents.anthropic_agent import AnthropicAgent

        provider = make_mock_provider()
        agent = AnthropicAgent(make_config(), provider, stt=None)

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            tmp_path = f.name

        msg = make_audio_message(tmp_path)
        pool = make_pool()

        # Act
        response = await process(agent, msg, pool)

        # Assert
        assert "not supported" in response.content.lower()
        provider.complete.assert_not_called()

    async def test_audio_tmp_file_deleted_on_success(self) -> None:
        """Temp file is unlinked after a successful transcription + LLM call."""
        # Arrange
        from lyra.agents.anthropic_agent import AnthropicAgent

        stt = make_mock_stt(TranscriptionResult("Hello world", "en", 1.5))
        provider = make_mock_provider("ok")
        agent = AnthropicAgent(make_config(), provider, stt=stt)

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            tmp_path = f.name

        assert Path(tmp_path).exists()
        msg = make_audio_message(tmp_path)
        pool = make_pool()

        # Act
        await process(agent, msg, pool)

        # Assert
        assert not Path(tmp_path).exists()

    async def test_audio_tmp_file_deleted_on_exception(self) -> None:
        """Temp file is unlinked even when transcription raises an exception."""
        # Arrange
        from lyra.agents.anthropic_agent import AnthropicAgent

        stt = make_mock_stt(raises=RuntimeError("boom"))
        provider = make_mock_provider()
        agent = AnthropicAgent(make_config(), provider, stt=stt)

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            tmp_path = f.name

        assert Path(tmp_path).exists()
        msg = make_audio_message(tmp_path)
        pool = make_pool()

        # Act — exception propagates but file must still be deleted
        with pytest.raises(RuntimeError):
            await process(agent, msg, pool)

        # Assert
        assert not Path(tmp_path).exists()

    async def test_audio_tmp_file_deleted_no_stt(self) -> None:
        """Temp file is unlinked even when stt=None (unsupported path)."""
        # Arrange
        from lyra.agents.anthropic_agent import AnthropicAgent

        provider = make_mock_provider()
        agent = AnthropicAgent(make_config(), provider, stt=None)

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            tmp_path = f.name

        assert Path(tmp_path).exists()
        msg = make_audio_message(tmp_path)
        pool = make_pool()

        # Act
        await process(agent, msg, pool)

        # Assert
        assert not Path(tmp_path).exists()

    async def test_audio_tmp_file_deleted_on_empty_transcript(self) -> None:
        """SC7: tmp file is deleted even when transcript is empty."""
        from lyra.agents.anthropic_agent import AnthropicAgent

        stt = make_mock_stt(TranscriptionResult("", "en", 0.5))
        provider = make_mock_provider()
        agent = AnthropicAgent(make_config(), provider, stt=stt)
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            tmp_path = f.name
        msg = make_audio_message(tmp_path)
        pool = make_pool()
        await process(agent, msg, pool)
        assert not Path(tmp_path).exists()

    async def test_audio_tmp_file_deleted_on_noise_transcript(self) -> None:
        """SC7: tmp file is deleted even when transcript is noise-only."""
        from lyra.agents.anthropic_agent import AnthropicAgent

        stt = make_mock_stt(TranscriptionResult("[Music]", "en", 0.5))
        provider = make_mock_provider()
        agent = AnthropicAgent(make_config(), provider, stt=stt)
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            tmp_path = f.name
        msg = make_audio_message(tmp_path)
        pool = make_pool()
        await process(agent, msg, pool)
        assert not Path(tmp_path).exists()

    async def test_text_message_unaffected(self) -> None:
        """Non-AUDIO messages follow the normal path — AUDIO branch not entered."""
        # Arrange
        from lyra.agents.anthropic_agent import AnthropicAgent

        stt = make_mock_stt(TranscriptionResult("should not be called", "en", 0.0))
        provider = make_mock_provider("text response")
        agent = AnthropicAgent(make_config(), provider, stt=stt)

        msg = make_text_message("tell me something")
        pool = make_pool()

        # Act
        response = await process(agent, msg, pool)

        # Assert — STT was never called
        cast(AsyncMock, stt.transcribe).assert_not_called()
        assert response.content == "text response"

        # Assert — sdk_history stores the literal user text
        user_history = next(m for m in pool.sdk_history if m["role"] == "user")
        assert user_history["content"] == "tell me something"
