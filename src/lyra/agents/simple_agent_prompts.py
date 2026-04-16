"""
SimpleAgent prompt building utilities.

Extracted from simple_agent.py to reduce file size (issue #753).
Provides helper functions for constructing LLM prompt text from user messages.
"""

from __future__ import annotations

import html
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lyra.core.message import InboundMessage
    from lyra.stt import STTProtocol, TranscriptionResult

log = logging.getLogger(__name__)


async def build_llm_text(
    msg: "InboundMessage",
    stt: "STTProtocol | None",
) -> tuple[str, str | None]:
    """Build the LLM prompt text from an inbound message.

    Handles:
    - Audio attachments (STT transcription)
    - Voice modality messages (wrap in voice_transcript tags)
    - Regular messages (wrap in user_message tags unless processor-enriched)

    Args:
        msg: The inbound message to process
        stt: STT provider for audio transcription (may be None)

    Returns:
        Tuple of (llm_text, transcription_text).
        - llm_text: The text to send to the LLM (wrapped in tags)
        - transcription_text: Raw STT text for history, or None if not voice/audio

    Raises:
        STTError: If STT transcription fails
    """
    # Handle audio messages - attachments with type="audio"
    audio_attachment = next((a for a in msg.attachments if a.type == "audio"), None)
    if audio_attachment is not None:
        # Post-#534 Slice 1: STT-None case is filtered at the pipeline stage
        # (MessagePipeline._run_stt_stage) before agents are invoked. By the
        # time we reach this branch, stt is guaranteed non-None.
        assert stt is not None
        tmp_path = Path(str(audio_attachment.url_or_path_or_bytes))
        return await _build_audio_text(tmp_path, stt)

    if msg.modality == "voice":
        # Pipeline-transcribed audio - wrap for prompt injection guard (H-8)
        return f"<voice_transcript>{html.escape(msg.text)}</voice_transcript>", msg.text

    if not msg.processor_enriched:
        return f"<user_message>{html.escape(msg.text)}</user_message>", None

    return msg.text, None


async def _build_audio_text(
    tmp_path: Path,
    stt: "STTProtocol",
) -> tuple[str, str]:
    """Build LLM text from an audio attachment via STT.

    Args:
        tmp_path: Path to the audio file
        stt: STT provider

    Returns:
        Tuple of (llm_text, transcription_text)

    Raises:
        STTError: If transcription fails
    """
    from lyra.stt import is_whisper_noise

    try:
        stt_result: TranscriptionResult = await stt.transcribe(tmp_path)
    except Exception as exc:
        raise STTError(str(exc)) from exc
    finally:
        tmp_path.unlink(missing_ok=True)

    if is_whisper_noise(stt_result.text):
        raise STTNoiseError(stt_result.text)

    escaped = html.escape(stt_result.text)
    return f"<voice_transcript>{escaped}</voice_transcript>", stt_result.text


class STTNoiseError(Exception):
    """Raised when STT detects only noise/silence."""

    def __init__(self, text: str) -> None:
        self.text = text
        super().__init__(f"STT detected noise: {text[:50]}...")


class STTError(Exception):
    """Raised when STT transcription fails."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
