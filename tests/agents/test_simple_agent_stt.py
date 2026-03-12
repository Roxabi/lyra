"""Integration tests for SimpleAgent AUDIO branch (T3.5).

Covers SC2–SC11 for SimpleAgent:
  - AUDIO success: prefixed transcript sent to CLI, Response returned
  - AUDIO empty transcript: retry prompt Response
  - AUDIO noise transcript: retry prompt Response
  - AUDIO transcription exception: error Response
  - AUDIO stt=None: unsupported Response
  - Temp file deleted in all paths (success, empty, exception, no-STT)
  - Non-AUDIO text message unaffected (regression guard)
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock

from lyra.agents.simple_agent import SimpleAgent
from lyra.core.agent import Agent, ModelConfig
from lyra.core.auth import TrustLevel
from lyra.core.cli_pool import CliResult
from lyra.core.message import (
    AudioContent,
    Message,
    MessageType,
    Platform,
    Response,
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
        trust_level=TrustLevel.TRUSTED,
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
        trust_level=TrustLevel.TRUSTED,
        platform_context=TelegramContext(chat_id=42),
    )


def make_pool(pool_id: str = "telegram:main:alice") -> Pool:
    return Pool(pool_id=pool_id, agent_name="lyra", hub=MagicMock())


def make_config() -> Agent:
    return Agent(
        name="lyra",
        system_prompt="You are Lyra.",
        memory_namespace="lyra",
        model_config=ModelConfig(),
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


def make_cli_pool(result: str = "cli response") -> MagicMock:
    cli_pool = MagicMock()
    cli_pool.send = AsyncMock(return_value=CliResult(result=result, session_id="s1"))
    return cli_pool


# ---------------------------------------------------------------------------
# TestSimpleAgentAudioBranch
# ---------------------------------------------------------------------------


class TestSimpleAgentAudioBranch:
    async def test_audio_success(self) -> None:
        """AUDIO with valid transcript: prefixed text sent to CLI, Response returned."""
        # Arrange
        stt = make_mock_stt(TranscriptionResult("Hello world", "en", 1.5))
        cli_pool = make_cli_pool("Sure, here's my reply.")
        agent = SimpleAgent(make_config(), cli_pool, stt=stt)

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            tmp_path = f.name

        msg = make_audio_message(tmp_path)
        pool = make_pool()

        # Act
        response = await agent.process(msg, pool)

        # Assert — CLI was called with the prefixed transcript
        cli_pool.send.assert_awaited_once()
        call_args = cli_pool.send.call_args
        text_sent = call_args[0][1]
        assert "🎤 [transcribed]: Hello world" in text_sent

        # Assert — Response contains the CLI reply
        assert isinstance(response, Response)
        assert response.content == "Sure, here's my reply."

    async def test_audio_empty_transcript(self) -> None:
        """AUDIO with empty transcript: retry prompt Response, CLI not called."""
        # Arrange
        stt = make_mock_stt(TranscriptionResult("", "en", 0.5))
        cli_pool = make_cli_pool()
        agent = SimpleAgent(make_config(), cli_pool, stt=stt)

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            tmp_path = f.name

        msg = make_audio_message(tmp_path)
        pool = make_pool()

        # Act
        response = await agent.process(msg, pool)

        # Assert
        assert isinstance(response, Response)
        assert "couldn't make out" in response.content.lower()
        cli_pool.send.assert_not_called()

    async def test_audio_noise_transcript(self) -> None:
        """AUDIO with Whisper noise token: retry prompt Response."""
        # Arrange
        stt = make_mock_stt(TranscriptionResult("[Music]", "en", 0.5))
        cli_pool = make_cli_pool()
        agent = SimpleAgent(make_config(), cli_pool, stt=stt)

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            tmp_path = f.name

        msg = make_audio_message(tmp_path)
        pool = make_pool()

        # Act
        response = await agent.process(msg, pool)

        # Assert
        assert isinstance(response, Response)
        assert "couldn't make out" in response.content.lower()
        cli_pool.send.assert_not_called()

    async def test_audio_transcription_exception(self) -> None:
        """AUDIO with transcription exception: error Response, no crash."""
        # Arrange
        stt = make_mock_stt(raises=RuntimeError("transcription failed"))
        cli_pool = make_cli_pool()
        agent = SimpleAgent(make_config(), cli_pool, stt=stt)

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            tmp_path = f.name

        msg = make_audio_message(tmp_path)
        pool = make_pool()

        # Act
        response = await agent.process(msg, pool)

        # Assert
        assert isinstance(response, Response)
        assert "couldn't transcribe" in response.content.lower()
        assert response.metadata.get("error") is True
        cli_pool.send.assert_not_called()

    async def test_audio_no_stt(self) -> None:
        """AUDIO with stt=None: unsupported Response, CLI not called."""
        # Arrange
        cli_pool = make_cli_pool()
        agent = SimpleAgent(make_config(), cli_pool, stt=None)

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            tmp_path = f.name

        msg = make_audio_message(tmp_path)
        pool = make_pool()

        # Act
        response = await agent.process(msg, pool)

        # Assert
        assert isinstance(response, Response)
        assert "not supported" in response.content.lower()
        cli_pool.send.assert_not_called()

    async def test_audio_tmp_file_deleted_on_success(self) -> None:
        """Temp file is unlinked after a successful transcription."""
        # Arrange
        stt = make_mock_stt(TranscriptionResult("Hello world", "en", 1.5))
        cli_pool = make_cli_pool()
        agent = SimpleAgent(make_config(), cli_pool, stt=stt)

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            tmp_path = f.name

        assert Path(tmp_path).exists()
        msg = make_audio_message(tmp_path)
        pool = make_pool()

        # Act
        await agent.process(msg, pool)

        # Assert
        assert not Path(tmp_path).exists()

    async def test_audio_tmp_file_deleted_on_empty_transcript(self) -> None:
        """Temp file is unlinked even for empty transcript (retry prompt path)."""
        # Arrange
        stt = make_mock_stt(TranscriptionResult("", "en", 0.5))
        cli_pool = make_cli_pool()
        agent = SimpleAgent(make_config(), cli_pool, stt=stt)

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            tmp_path = f.name

        assert Path(tmp_path).exists()
        msg = make_audio_message(tmp_path)
        pool = make_pool()

        # Act
        await agent.process(msg, pool)

        # Assert
        assert not Path(tmp_path).exists()

    async def test_audio_tmp_file_deleted_on_exception(self) -> None:
        """Temp file is unlinked even when transcription raises an exception."""
        # Arrange
        stt = make_mock_stt(raises=RuntimeError("boom"))
        cli_pool = make_cli_pool()
        agent = SimpleAgent(make_config(), cli_pool, stt=stt)

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            tmp_path = f.name

        assert Path(tmp_path).exists()
        msg = make_audio_message(tmp_path)
        pool = make_pool()

        # Act — SimpleAgent catches the exception and returns error Response
        response = await agent.process(msg, pool)

        # Assert — file deleted despite exception
        assert not Path(tmp_path).exists()
        assert response.metadata.get("error") is True

    async def test_audio_tmp_file_deleted_no_stt(self) -> None:
        """Temp file is unlinked even when stt=None (unsupported path)."""
        # Arrange
        cli_pool = make_cli_pool()
        agent = SimpleAgent(make_config(), cli_pool, stt=None)

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            tmp_path = f.name

        assert Path(tmp_path).exists()
        msg = make_audio_message(tmp_path)
        pool = make_pool()

        # Act
        await agent.process(msg, pool)

        # Assert
        assert not Path(tmp_path).exists()

    async def test_text_message_unaffected(self) -> None:
        """Non-AUDIO messages follow the normal CLI path — AUDIO branch not entered."""
        # Arrange
        stt = make_mock_stt(TranscriptionResult("should not be called", "en", 0.0))
        cli_pool = make_cli_pool("text response")
        agent = SimpleAgent(make_config(), cli_pool, stt=stt)

        msg = make_text_message("tell me something")
        pool = make_pool()

        # Act
        response = await agent.process(msg, pool)

        # Assert — STT was never called
        cast(AsyncMock, stt.transcribe).assert_not_called()
        assert response.content == "text response"

        # Assert — CLI was called with the literal text
        call_args = cli_pool.send.call_args
        text_sent = call_args[0][1]
        assert text_sent == "tell me something"
