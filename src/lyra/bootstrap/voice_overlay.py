"""Voice overlay helpers — STT config overlay and service initialisation."""

from __future__ import annotations

import dataclasses
import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lyra.core.agent_config import Agent

from lyra.core.agent_config import AgentSTTConfig
from lyra.stt import STTConfig, STTService, load_stt_config
from lyra.tts import TTSService, load_tts_config

log = logging.getLogger(__name__)


def apply_agent_stt_overlay(
    agent_stt: AgentSTTConfig | None,
    stt_cfg: STTConfig,
) -> STTConfig:
    """Overlay non-None fields from AgentSTTConfig onto STTConfig.

    None fields in agent_stt are skipped — voicecli global defaults remain in effect.
    Returns an updated STTConfig (via model_copy).
    """
    if agent_stt is None:
        return stt_cfg
    a = agent_stt
    updates: dict[str, object] = {}
    if a.language_detection_threshold is not None:
        updates["language_detection_threshold"] = a.language_detection_threshold
    if a.language_detection_segments is not None:
        updates["language_detection_segments"] = a.language_detection_segments
    if a.language_fallback is not None:
        updates["language_fallback"] = a.language_fallback
    return stt_cfg.model_copy(update=updates) if updates else stt_cfg


def init_stt(first_agent_config: "Agent") -> STTService | None:
    """Initialise STT service if STT_MODEL_SIZE is set."""
    if not os.environ.get("STT_MODEL_SIZE"):
        return None
    try:
        agent_stt = first_agent_config.voice.stt if first_agent_config.voice else None
        stt_cfg = apply_agent_stt_overlay(agent_stt, load_stt_config())
        stt_service = STTService(stt_cfg)
        log.info("STT enabled: model=%s (via voiceCLI)", stt_cfg.model_size)
        return stt_service
    except ValueError as exc:
        raise SystemExit(f"Invalid STT configuration: {exc}") from exc


def init_tts(
    stt_service: STTService | None,
) -> TTSService | None:
    """Initialise TTS service from global env-var defaults.

    Per-agent TTS config is resolved at synthesis time via
    Hub.dispatch_response() → resolve_binding() → agent.config.voice.tts.
    """
    voice_responses = os.environ.get("LYRA_VOICE_RESPONSES", "1") != "0"
    if stt_service is None or not voice_responses:
        return None

    tts_cfg = load_tts_config()
    tts_service = TTSService(tts_cfg)
    log.info(
        "TTS voice responses enabled (global defaults): engine=%s voice=%s",
        tts_cfg.engine or "default",
        tts_cfg.voice or "default",
    )
    return tts_service
