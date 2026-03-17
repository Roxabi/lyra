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
    ModelConfig,
    SmartRoutingConfig,
)
from .persona import compose_system_prompt, load_persona

log = logging.getLogger(__name__)


def agent_row_to_config(  # noqa: C901, PLR0915 — each branch handles one optional field
    row: "AgentRow",
    instance_overrides: dict | None = None,
) -> "Agent":
    """Convert an AgentRow (from AgentStore cache) into an Agent config dataclass.

    Mirrors load_agent_config() logic but reads from AgentRow instead of TOML.
    Machine-local instance_overrides are applied with the same priority as in
    load_agent_config(): TOML/DB value wins, overrides fill gaps.
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

    # Persona: DB row wins, then instance_overrides
    persona_name = row.persona or overrides.get("persona")
    persona = None
    if persona_name:
        try:
            persona = load_persona(persona_name)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "Agent %r: failed to load persona %r -- %s",
                row.name,
                persona_name,
                exc,
            )

    # System prompt from persona (no [prompt].system in DB rows)
    system_prompt = compose_system_prompt(persona) if persona else ""

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

    # Deserialize tts_json -> AgentTTSConfig
    agent_tts: AgentTTSConfig | None = None
    if row.tts_json:
        tts_data: dict = json.loads(row.tts_json)
        _tts_known = {f.name for f in AgentTTSConfig.__dataclass_fields__.values()}
        _tts_extra = set(tts_data) - _tts_known
        if _tts_extra:
            log.warning(
                "agent_row_to_config(%s): unknown tts_json keys: %s",
                row.name,
                _tts_extra,
            )
        agent_tts = _build_tts_from_dict(tts_data)

    # Deserialize stt_json -> AgentSTTConfig
    agent_stt: AgentSTTConfig | None = None
    if row.stt_json:
        stt_data: dict = json.loads(row.stt_json)
        _stt_known = {f.name for f in AgentSTTConfig.__dataclass_fields__.values()}
        _stt_extra = set(stt_data) - _stt_known
        if _stt_extra:
            log.warning(
                "agent_row_to_config(%s): unknown stt_json keys: %s",
                row.name,
                _stt_extra,
            )
        agent_stt = _build_stt_from_dict(stt_data)

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

    # i18n language from DB
    i18n_language = _validate_i18n_language(row.i18n_language or "en", row.name)

    return _assemble_agent(
        name=row.name,
        system_prompt=system_prompt,
        memory_namespace=memory_namespace,
        model_config=model_cfg,
        permissions=permissions,
        commands=commands,
        plugins_enabled=tuple(plugins),
        persona=persona,
        i18n_language=i18n_language,
        smart_routing=smart_routing,
        show_intermediate=row.show_intermediate,
        workspaces=workspaces,
        tts=agent_tts,
        stt=agent_stt,
    )
