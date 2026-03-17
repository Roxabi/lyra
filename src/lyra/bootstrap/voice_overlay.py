"""Voice overlay helpers — TTS/STT config overlay and service initialisation."""

from __future__ import annotations

import dataclasses
import logging
import os

from lyra.core.agent import Agent
from lyra.core.agent_config import AgentSTTConfig, AgentTTSConfig
from lyra.stt import STTConfig, STTService, load_stt_config
from lyra.tts import TTSConfig, TTSService, load_tts_config

log = logging.getLogger(__name__)


def apply_agent_tts_overlay(
    agent_tts: AgentTTSConfig | None,
    tts_cfg: TTSConfig,
) -> TTSConfig:
    """Overlay non-None fields from AgentTTSConfig onto TTSConfig.

    None fields in agent_tts are skipped — voicecli global defaults remain in effect.
    Returns an updated TTSConfig (via dataclasses.replace).
    """
    if agent_tts is None:
        return tts_cfg
    a = agent_tts
    if a.engine is not None:
        tts_cfg = dataclasses.replace(tts_cfg, engine=a.engine)
    if a.voice is not None:
        tts_cfg = dataclasses.replace(tts_cfg, voice=a.voice)
    if a.language is not None:
        tts_cfg = dataclasses.replace(tts_cfg, language=a.language)
    return tts_cfg


def apply_agent_stt_overlay(
    agent_stt: AgentSTTConfig | None,
    stt_cfg: STTConfig,
) -> STTConfig:
    """Overlay non-None fields from AgentSTTConfig onto STTConfig.

    None fields in agent_stt are skipped — voicecli global defaults remain in effect.
    Returns an updated STTConfig (via dataclasses.replace).
    """
    if agent_stt is None:
        return stt_cfg
    a = agent_stt
    if a.language_detection_threshold is not None:
        stt_cfg = dataclasses.replace(
            stt_cfg, language_detection_threshold=a.language_detection_threshold
        )
    if a.language_detection_segments is not None:
        stt_cfg = dataclasses.replace(
            stt_cfg, language_detection_segments=a.language_detection_segments
        )
    if a.language_fallback is not None:
        stt_cfg = dataclasses.replace(stt_cfg, language_fallback=a.language_fallback)
    return stt_cfg


def init_stt(first_agent_config: Agent) -> STTService | None:
    """Initialise STT service if STT_MODEL_SIZE is set."""
    if not os.environ.get("STT_MODEL_SIZE"):
        return None
    try:
        stt_cfg = apply_agent_stt_overlay(first_agent_config.stt, load_stt_config())
        stt_service = STTService(stt_cfg)
        log.info("STT enabled: model=%s (via voiceCLI)", stt_cfg.model_size)
        return stt_service
    except ValueError as exc:
        raise SystemExit(f"Invalid STT configuration: {exc}") from exc


def init_tts(
    first_agent_config: Agent,
    stt_service: STTService | None,
    agent_configs: dict[str, Agent],
    first_agent_name: str,
) -> TTSService | None:
    """Initialise TTS service if STT is active and voice responses are enabled."""
    voice_responses = os.environ.get("LYRA_VOICE_RESPONSES", "1") != "0"
    if stt_service is None or not voice_responses:
        return None

    tts_cfg = apply_agent_tts_overlay(first_agent_config.tts, load_tts_config())
    _agents_with_tts = [n for n, cfg in agent_configs.items() if cfg.tts is not None]
    if len(_agents_with_tts) > 1:
        log.warning(
            "Multiple agents define [tts] config: %s — "
            "using %r (first alphabetically). "
            "Other agents' [tts] settings are ignored.",
            _agents_with_tts,
            first_agent_name,
        )
    tts_service = TTSService(tts_cfg)
    log.info(
        "TTS voice responses enabled: engine=%s voice=%s",
        tts_cfg.engine or "default",
        tts_cfg.voice or "default",
    )
    return tts_service
