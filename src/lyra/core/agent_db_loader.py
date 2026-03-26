from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .agent_config import Agent
    from .agent_models import AgentRow

from .agent_builder import (
    _assemble_agent,
    _build_commands_from_dict,
    _build_smart_routing_from_dict,
    _build_stt_from_dict,
    _build_tts_from_dict,
    _resolve_cwd,
    _resolve_workspaces_lenient,
    _validate_backend_model,
    _validate_i18n_language,
)
from .agent_config import (
    AgentSTTConfig,
    AgentTTSConfig,
    AgentVoiceConfig,
    ModelConfig,
    SmartRoutingConfig,
)
from .persona import compose_system_prompt_from_json

log = logging.getLogger(__name__)


def agent_row_to_config(  # noqa: C901, PLR0915 — each branch handles one optional field
    row: "AgentRow",
    instance_overrides: dict | None = None,
) -> "Agent":
    """Convert an AgentRow (from AgentStore cache) into an Agent config dataclass.

    This is the single loading path — TOML loading was removed in #346.
    """
    overrides: dict = instance_overrides or {}

    # Parse JSON columns
    tools: list[str] = json.loads(row.tools_json) if row.tools_json else []
    plugins: list[str] = json.loads(row.plugins_json) if row.plugins_json else []

    # Resolve cwd: DB row wins, then instance_overrides, then None
    cwd = _resolve_cwd(row.cwd or overrides.get("cwd"), row.name)

    _validate_backend_model(row.backend, row.model, row.name)

    model_cfg = ModelConfig(
        backend=row.backend,
        model=row.model,
        max_turns=row.max_turns,
        tools=tuple(tools),
        cwd=cwd,
        skip_permissions=row.skip_permissions,
        streaming=row.streaming,
    )

    # Persona: always from persona_json (inline JSON)
    system_prompt = ""
    if row.persona_json:
        persona_dict = json.loads(row.persona_json)
        system_prompt = compose_system_prompt_from_json(persona_dict)

    # Smart routing
    smart_routing: SmartRoutingConfig | None = None
    if row.smart_routing_json is not None:
        smart_routing = _build_smart_routing_from_dict(
            json.loads(row.smart_routing_json)
        )

    # Workspaces: DB row wins per-key, instance_overrides fill gaps
    db_workspaces: dict = json.loads(row.workspaces_json) if row.workspaces_json else {}
    workspaces_raw: dict = {**overrides.get("workspaces", {}), **db_workspaces}
    workspaces = _resolve_workspaces_lenient(workspaces_raw, row.name)

    memory_namespace = row.memory_namespace or row.name

    # Voice config: read from voice_json ({"tts": {...}, "stt": {...}})
    voice: AgentVoiceConfig | None = None
    if row.voice_json:
        voice_data: dict = json.loads(row.voice_json)
        tts_data = voice_data.get("tts", {})
        stt_data = voice_data.get("stt", {})

        agent_tts: AgentTTSConfig | None = None
        agent_stt: AgentSTTConfig | None = None

        if tts_data:
            _tts_known = set(AgentTTSConfig.model_fields)
            _tts_extra = set(tts_data) - _tts_known
            if _tts_extra:
                log.warning(
                    "agent_row_to_config(%s): unknown voice_json.tts keys: %s",
                    row.name,
                    _tts_extra,
                )
            agent_tts = _build_tts_from_dict(tts_data)

        if stt_data:
            _stt_known = set(AgentSTTConfig.model_fields)
            _stt_extra = set(stt_data) - _stt_known
            if _stt_extra:
                log.warning(
                    "agent_row_to_config(%s): unknown voice_json.stt keys: %s",
                    row.name,
                    _stt_extra,
                )
            agent_stt = _build_stt_from_dict(stt_data)

        voice = AgentVoiceConfig(
            tts=agent_tts or AgentTTSConfig(),
            stt=agent_stt or AgentSTTConfig(),
        )

    # Permissions from DB
    permissions: tuple[str, ...] = tuple(
        json.loads(row.permissions_json) if row.permissions_json else []
    )

    # Commands from DB
    commands = (
        _build_commands_from_dict(json.loads(row.commands_json))
        if row.commands_json
        else {}
    )

    # fallback_language
    i18n_language = _validate_i18n_language(row.fallback_language or "en", row.name)

    # Bridge fallback_language into STT language_fallback if not set
    if voice and voice.stt.language_fallback is None and i18n_language:
        patched_stt = voice.stt.model_copy(update={"language_fallback": i18n_language})
        voice = AgentVoiceConfig(tts=voice.tts, stt=patched_stt)

    # Patterns: configurable rewrite rules (#345)
    if row.patterns_json:
        _patterns_raw = json.loads(row.patterns_json)
        patterns: dict[str, bool] = (
            {k: bool(v) for k, v in _patterns_raw.items()}
            if isinstance(_patterns_raw, dict)
            else {}
        )
        if not isinstance(_patterns_raw, dict):
            log.warning(
                "agent_row_to_config(%s): patterns_json is not a dict (%s) — ignored",
                row.name,
                type(_patterns_raw).__name__,
            )
    else:
        patterns: dict[str, bool] = {}

    # Passthroughs: commands forwarded straight to the LLM
    passthroughs: tuple[str, ...] = tuple(
        json.loads(row.passthroughs_json) if row.passthroughs_json else []
    )

    return _assemble_agent(
        name=row.name,
        system_prompt=system_prompt,
        memory_namespace=memory_namespace,
        llm_config=model_cfg,
        permissions=permissions,
        commands=commands,
        commands_enabled=tuple(plugins),
        i18n_language=i18n_language,
        smart_routing=smart_routing,
        show_intermediate=row.show_intermediate,
        show_tool_recap=row.show_tool_recap,
        workspaces=workspaces,
        voice=voice,
        patterns=patterns,
        passthroughs=passthroughs,
    )
