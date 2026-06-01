"""TTS text normalization — markdown stripping and whitespace cleanup."""

from __future__ import annotations

import re

# Precompiled regex patterns for TTS text normalization
_MD_BOLD = re.compile(r"\*\*([^*]+)\*\*")  # **bold**
_MD_ITALIC = re.compile(r"\*([^*]+)\*")  # *italic* (single)
_MD_UNDERLINE = re.compile(r"_([^_]+)_")  # _underline_
_MD_CODE = re.compile(r"`([^`]+)`")  # `code`
_MD_HEADING = re.compile(r"^#{1,6}\s*", re.MULTILINE)  # # heading
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")  # [text](url)
_URL = re.compile(r"https?://[^\s<>)\"']+")
_MULTI_SPACE = re.compile(r"\s+")


def normalize_text_for_tts(text: str) -> str:
    """Normalize text for TTS synthesis.

    Transforms:
    - Markdown syntax (**, *, _, `, #) → stripped
    - Links [text](url) → text
    - URLs → "link" placeholder
    - Multiple spaces → single space
    - Newlines → space

    Returns normalized text ready for voicecli.
    """
    # Strip markdown heading markers
    text = _MD_HEADING.sub("", text)

    # Convert markdown links: [text](url) → text
    text = _MD_LINK.sub(r"\1", text)

    # Strip markdown formatting (order matters: bold before italic)
    text = _MD_BOLD.sub(r"\1", text)
    text = _MD_ITALIC.sub(r"\1", text)
    text = _MD_UNDERLINE.sub(r"\1", text)
    text = _MD_CODE.sub(r"\1", text)

    # Replace URLs with "link" placeholder
    text = _URL.sub("link", text)

    # Collapse all whitespace (newlines, multiple spaces) to single space
    text = _MULTI_SPACE.sub(" ", text).strip()

    return text
