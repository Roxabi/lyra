"""
SimpleAgent prompt building utilities.

Extracted from simple_agent.py to reduce file size (issue #753).
Provides helper functions for constructing LLM prompt text from user messages.
"""

from __future__ import annotations

import asyncio
import html
import logging
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from lyra.core.ports.stt import STTNoiseError

if TYPE_CHECKING:
    from lyra.core.messaging.message import InboundMessage
    from lyra.core.ports.stt import STTProtocol, TranscriptionResult

log = logging.getLogger(__name__)


def _validate_audio_path(path: Path) -> Path:
    """Validate that the audio file path is safe (no directory traversal).

    Ensures the path is within the system temp directory, contains no
    parent-directory references, is a regular file owned by the current user,
    and is not a symlink.
    """
    if ".." in path.parts:
        raise ValueError(f"Invalid audio path: traversal detected in {path}")
    if path.is_symlink():
        raise ValueError("Invalid audio path: symlinks are not allowed")
    resolved = path.resolve()
    tmp_dir = Path(tempfile.gettempdir()).resolve()
    try:
        resolved.relative_to(tmp_dir)
    except ValueError as exc:
        raise ValueError(
            f"Invalid audio path: {path} is outside temp directory"
        ) from exc
    if not resolved.is_file():
        raise ValueError("Invalid audio path: not a regular file")
    if resolved.stat().st_uid != os.getuid():
        raise ValueError("Invalid audio path: file not owned by current user")
    return resolved


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
        raw_path = str(audio_attachment.url_or_path_or_bytes)
        tmp_path = _validate_audio_path(Path(raw_path))
        return await _build_audio_text(tmp_path, stt)

    if msg.modality == "voice":
        # Pipeline-transcribed audio - wrap for prompt injection guard (H-8)
        return f"<voice_transcript>{html.escape(msg.text)}</voice_transcript>", msg.text

    if not msg.processor_enriched:
        return f"<user_message>{html.escape(msg.text)}</user_message>", None

    return msg.text, None


def _safe_read_bytes(path: Path) -> bytes:
    """Read file bytes without following symlinks (O_NOFOLLOW).

    Prevents TOCTOU race where a path is swapped for a symlink after
    validation but before read.
    """
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    try:
        chunks = []
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


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
    from lyra.nats.stt_helpers import is_whisper_noise, mime_from_suffix

    try:
        audio_bytes = await asyncio.to_thread(_safe_read_bytes, tmp_path)
        mime = mime_from_suffix(tmp_path.suffix)
        stt_result: TranscriptionResult = await stt.transcribe(audio_bytes, mime)
    except Exception as exc:
        raise STTError(str(exc)) from exc
    finally:
        tmp_path.unlink(missing_ok=True)

    if is_whisper_noise(stt_result.text):
        raise STTNoiseError(stt_result.text)

    escaped = html.escape(stt_result.text)
    return f"<voice_transcript>{escaped}</voice_transcript>", stt_result.text


class STTError(Exception):
    """Raised when STT transcription fails."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
