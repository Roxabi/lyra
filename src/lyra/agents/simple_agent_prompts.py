"""
SimpleAgent prompt building utilities.

Extracted from simple_agent.py to reduce file size (issue #753).
Provides helper functions for constructing LLM prompt text from user messages.
"""

from __future__ import annotations

import html
import logging
from typing import TYPE_CHECKING

from lyra.core.ports.stt import (
    STTNoiseError as STTNoiseError,
)  # re-export (guard #1225)

if TYPE_CHECKING:
    from lyra.core.messaging.message import InboundMessage

log = logging.getLogger(__name__)


async def build_llm_text(
    msg: "InboundMessage",
) -> tuple[str, str | None]:
    """Build the LLM prompt text from an inbound message.

    Handles:
    - Voice modality messages (wrap in voice_transcript tags)
    - Regular messages (wrap in user_message tags unless processor-enriched)

    Args:
        msg: The inbound message to process.

    Returns:
        Tuple of (llm_text, transcription_text).
        - llm_text: The text to send to the LLM (wrapped in tags)
        - transcription_text: Raw STT text for history, or None if not voice

    """
    if any(a.type == "audio" for a in msg.attachments):
        log.warning(
            "build_llm_text: stray audio attachment (type='audio') detected — "
            "protocol violation, skipping"
        )

    if msg.modality == "voice":
        # Pipeline-transcribed audio - wrap for prompt injection guard (H-8)
        return f"<voice_transcript>{html.escape(msg.text)}</voice_transcript>", msg.text

    if not msg.processor_enriched:
        return f"<user_message>{html.escape(msg.text)}</user_message>", None

    return msg.text, None
