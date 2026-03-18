"""SimpleAgent AUDIO branch — temp file cleanup tests.

Covers:
  - Temp file deleted on successful transcription
  - Temp file deleted on empty transcript (retry prompt path)
  - Temp file deleted when transcription raises an exception
  - Temp file deleted when stt=None (unsupported path)
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from lyra.agents.simple_agent import SimpleAgent
from lyra.core.message import Response
from lyra.stt import TranscriptionResult

from .conftest import (
    make_audio_message,
    make_cli_pool,
    make_config,
    make_mock_stt,
    make_pool,
)


class TestSimpleAgentAudioBranch:
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
        assert isinstance(response, Response)
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
