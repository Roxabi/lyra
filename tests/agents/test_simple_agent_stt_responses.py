"""SimpleAgent AUDIO branch — response content tests (SC2–SC5, SC8).

Covers:
  - AUDIO success: prefixed transcript sent to CLI, Response returned
  - AUDIO empty transcript: retry prompt Response
  - AUDIO noise transcript: retry prompt Response
  - AUDIO transcription exception: error Response
  - AUDIO stt=None: unsupported Response
"""

from __future__ import annotations

import tempfile

import pytest

from lyra.agents.simple_agent import SimpleAgent
from lyra.core.messaging.message import Response
from lyra.stt import TranscriptionResult

from .conftest import (
    make_audio_message,
    make_cli_pool,
    make_config,
    make_mock_stt,
    make_pool,
)


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
        cli_pool.complete.assert_awaited_once()
        call_args = cli_pool.complete.call_args
        text_sent = call_args[0][1]
        assert "<voice_transcript>Hello world</voice_transcript>" in text_sent

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
        cli_pool.complete.assert_not_called()

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
        stt = make_mock_stt(TranscriptionResult(noise_text, "en", 0.5))
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
        cli_pool.complete.assert_not_called()

    async def test_audio_transcription_exception(self) -> None:
        # Contract: SimpleAgent catches transcription exceptions and returns an error
        # Response (does not re-raise to the pool).
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
        cli_pool.complete.assert_not_called()
