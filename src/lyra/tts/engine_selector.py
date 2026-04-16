"""TTS engine selection logic — config loading and parameter merging."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from pathlib import Path

    from lyra.core.agent_config import AgentTTSConfig


class TTSConfig(BaseModel):
    """Global TTS configuration from environment variables."""

    engine: str | None = None  # LYRA_TTS_ENGINE env var
    voice: str | None = None  # LYRA_TTS_VOICE env var
    language: str | None = None  # LYRA_TTS_LANGUAGE env var


def load_tts_config() -> TTSConfig:
    """Load TTS configuration from environment variables."""
    return TTSConfig(
        engine=os.environ.get("LYRA_TTS_ENGINE"),
        voice=os.environ.get("LYRA_TTS_VOICE"),
        language=os.environ.get("LYRA_TTS_LANGUAGE"),
    )


# qwen_tts expects full language names, not ISO 639-1 codes
LANG_ISO_TO_QWEN: dict[str, str] = {
    "zh": "chinese",
    "en": "english",
    "fr": "french",
    "de": "german",
    "it": "italian",
    "ja": "japanese",
    "ko": "korean",
    "pt": "portuguese",
    "ru": "russian",
    "es": "spanish",
}


def normalize_language(lang: str | None) -> str | None:
    """Convert ISO 639-1 language code to qwen_tts full name."""
    if lang is None:
        return None
    return LANG_ISO_TO_QWEN.get(lang.lower(), lang)


def build_generate_kwargs(  # noqa: PLR0913 -- merge-order inputs map 1:1 to config layers
    output: "Path",
    *,
    global_engine: str | None,
    global_voice: str | None,
    global_language: str | None,
    agent_tts: "AgentTTSConfig | None" = None,
    language: str | None = None,
    voice: str | None = None,
    fallback_language: str | None = None,
) -> dict:
    """Build kwargs for voicecli.generate_async with engine/voice/language selection.

    Merge order (high → low priority):
    - language/voice user-pref overrides
    - agent_tts per-agent config fields
    - fallback_language agent-level default
    - global_engine/global_voice/global_language defaults

    ``chunked`` is always ``True`` (safety hardcode, never overridden).
    """
    a = agent_tts

    # language: user pref > agent_tts > fallback_language > global
    if language is not None:
        effective_lang = language
    elif a is not None and a.language is not None:
        effective_lang = a.language
    elif fallback_language is not None:
        effective_lang = fallback_language
    else:
        effective_lang = global_language

    # voice: user pref > agent_tts > global
    if voice is not None:
        effective_voice = voice
    elif a is not None and a.voice is not None:
        effective_voice = a.voice
    else:
        effective_voice = global_voice

    # engine: agent_tts > global (no user-pref layer)
    effective_engine = (
        a.engine if a is not None and a.engine is not None else global_engine
    )

    kwargs: dict = {
        "output": output,
        "engine": effective_engine,
        "voice": effective_voice,
        "language": normalize_language(effective_lang),
        "chunked": True,
        "mp3": False,
    }

    if a is not None:
        for field_name in (
            "accent",
            "personality",
            "speed",
            "emotion",
            "exaggeration",
            "cfg_weight",
            "segment_gap",
            "crossfade",
            "chunk_size",
        ):
            val = getattr(a, field_name, None)
            if val is not None:
                kwargs[field_name] = val

    return kwargs
