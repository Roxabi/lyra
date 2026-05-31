"""TTS engine selection logic — parameter merging for voiceCLI generate calls.

Adapter concern: voiceCLI-specific kwarg construction. Lives in lyra.nats (not core/)
because it references AgentTTSConfig (via TYPE_CHECKING) and is consumed exclusively
by nats_tts_client.py. Dead code TTSConfig/load_tts_config not included (issue #1221).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from lyra.core.agent.agent_config import AgentTTSConfig


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


@dataclass(frozen=True)
class GenerateKwargsDeps:
    """Frozen deps for build_generate_kwargs — inputs map 1:1 to config layers."""

    output: "Path"
    global_engine: str | None
    global_voice: str | None
    global_language: str | None
    agent_tts: "AgentTTSConfig | None" = field(default=None)
    language: str | None = field(default=None)
    voice: str | None = field(default=None)
    fallback_language: str | None = field(default=None)


def build_generate_kwargs(deps: GenerateKwargsDeps) -> dict:
    """Build kwargs for voicecli.generate_async with engine/voice/language selection.

    Merge order (high → low priority):
    - language/voice user-pref overrides
    - agent_tts per-agent config fields
    - fallback_language agent-level default
    - global_engine/global_voice/global_language defaults

    ``chunked`` is always ``True`` (safety hardcode, never overridden).
    """
    a = deps.agent_tts

    # language: user pref > agent_tts > fallback_language > global
    if deps.language is not None:
        effective_lang = deps.language
    elif a is not None and a.language is not None:
        effective_lang = a.language
    elif deps.fallback_language is not None:
        effective_lang = deps.fallback_language
    else:
        effective_lang = deps.global_language

    # voice: user pref > agent_tts > global
    if deps.voice is not None:
        effective_voice = deps.voice
    elif a is not None and a.voice is not None:
        effective_voice = a.voice
    else:
        effective_voice = deps.global_voice

    # engine: agent_tts > global (no user-pref layer)
    effective_engine = (
        a.engine if a is not None and a.engine is not None else deps.global_engine
    )

    kwargs: dict = {
        "output": deps.output,
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
