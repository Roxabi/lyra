from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from .agent_config import (
    _VALID_BACKENDS,
    _WORKSPACE_BUILTIN_CONFLICTS,
    Agent,
    AgentSTTConfig,
    AgentTTSConfig,
    AgentVoiceConfig,
    Complexity,
    ModelConfig,
    SmartRoutingConfig,
)

if TYPE_CHECKING:
    from .commands.command_router import CommandConfig

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared validation helpers
# ---------------------------------------------------------------------------


def _validate_backend_model(backend: str, model: str, agent_name: str) -> None:
    """Shared validation for backend and model values."""
    if backend not in _VALID_BACKENDS:
        raise ValueError(
            f"Invalid backend {backend!r} for agent {agent_name!r}: "
            f"must be one of {sorted(_VALID_BACKENDS)}"
        )
    if not re.match(r"^[a-zA-Z0-9_.:-]+$", model):
        raise ValueError(
            f"Invalid model {model!r} for agent {agent_name!r}: "
            "only [a-zA-Z0-9_.:-] characters allowed"
        )


def _resolve_cwd(raw_cwd: str | None, agent_name: str) -> Path | None:
    """Resolve a raw cwd string to an absolute Path, or None if absent/invalid."""
    if raw_cwd is None:
        return None
    resolved = Path(raw_cwd).expanduser().resolve()
    if not resolved.is_dir():
        log.warning(
            "Agent %r: cwd %r is not a directory — ignored",
            agent_name,
            raw_cwd,
        )
        return None
    return resolved


def _validate_i18n_language(raw: str, agent_name: str) -> str:
    """Return raw if it matches ^[a-z]{2,8}$, else warn and return 'en'."""
    if re.match(r"^[a-z]{2,8}$", raw):
        return raw
    log.warning(
        "Agent %r: invalid i18n_language %r — falling back to 'en'",
        agent_name,
        raw,
    )
    return "en"


# ---------------------------------------------------------------------------
# Shared builder helpers
# ---------------------------------------------------------------------------


def _build_smart_routing_from_dict(sr_data: dict) -> SmartRoutingConfig:
    """Build a SmartRoutingConfig from a parsed dict."""
    sr_models = sr_data.get("models", {})
    routing_table: dict[Complexity, str] = {}
    for level in Complexity:
        model_id = sr_models.get(level.value)
        if model_id:
            routing_table[level] = model_id
    hcc = sr_data.get("high_complexity_commands", [])
    return SmartRoutingConfig(
        enabled=bool(sr_data.get("enabled", False)),
        routing_table=routing_table,
        history_size=int(sr_data.get("history_size", 50)),
        high_complexity_commands=tuple(hcc),
    )


def _build_tts_from_dict(tts_data: dict) -> AgentTTSConfig:
    """Build an AgentTTSConfig from a parsed dict."""
    return AgentTTSConfig(
        engine=tts_data.get("engine"),
        voice=tts_data.get("voice"),
        language=tts_data.get("language"),
        accent=tts_data.get("accent"),
        personality=tts_data.get("personality"),
        speed=tts_data.get("speed"),
        emotion=tts_data.get("emotion"),
        segment_gap=int(tts_data["segment_gap"]) if "segment_gap" in tts_data else None,
        crossfade=int(tts_data["crossfade"]) if "crossfade" in tts_data else None,
        chunked=bool(tts_data["chunked"]) if "chunked" in tts_data else None,
        chunk_size=int(tts_data["chunk_size"]) if "chunk_size" in tts_data else None,
        exaggeration=(
            float(tts_data["exaggeration"]) if "exaggeration" in tts_data else None
        ),
        cfg_weight=float(tts_data["cfg_weight"]) if "cfg_weight" in tts_data else None,
    )


def _build_stt_from_dict(stt_data: dict) -> AgentSTTConfig:
    """Build an AgentSTTConfig from a parsed dict."""
    return AgentSTTConfig(
        language_detection_threshold=stt_data.get("language_detection_threshold"),
        language_detection_segments=stt_data.get("language_detection_segments"),
        language_fallback=stt_data.get("language_fallback"),
    )


def _build_commands_from_dict(commands_data: dict) -> dict:
    """Build a commands dict from a parsed dict."""
    from .commands.command_router import CommandConfig  # local: avoids cycle

    commands = {}
    for cmd_name, cmd_data in commands_data.items():
        commands[cmd_name] = CommandConfig(
            description=cmd_data.get("description", ""),
            builtin=bool(cmd_data.get("builtin", False)),
            timeout=float(cmd_data.get("timeout", 30.0)),
        )
    return commands


def _resolve_workspaces_lenient(
    workspaces_raw: dict,
    agent_name: str,
) -> dict[str, Path]:
    """Resolve workspace paths, logging warnings and skipping invalid entries."""
    workspaces: dict[str, Path] = {}
    for key, raw_path in workspaces_raw.items():
        if not re.match(r"^[a-zA-Z0-9_-]+$", key):
            log.warning(
                "Agent %r: invalid workspace name %r -- skipping",
                agent_name,
                key,
            )
            continue
        if key in _WORKSPACE_BUILTIN_CONFLICTS:
            log.warning(
                "Agent %r: workspace key %r clashes with built-in"
                " command /%s -- skipping",
                agent_name,
                key,
                key,
            )
            continue
        resolved_ws = Path(raw_path).expanduser().resolve()
        if not resolved_ws.is_dir():
            log.warning(
                "Agent %r: workspace %r path %r is not a directory -- skipping",
                agent_name,
                key,
                raw_path,
            )
            continue
        workspaces[key] = resolved_ws
    return workspaces


# ---------------------------------------------------------------------------
# Final Agent assembly
# ---------------------------------------------------------------------------


def _assemble_agent(  # noqa: PLR0913 — one param per Agent field
    *,
    name: str,
    system_prompt: str,
    memory_namespace: str,
    llm_config: ModelConfig,
    permissions: tuple[str, ...],
    commands: dict[str, "CommandConfig"],
    commands_enabled: tuple[str, ...],
    i18n_language: str,
    smart_routing: SmartRoutingConfig | None,
    show_intermediate: bool,
    show_tool_recap: bool = True,
    workspaces: dict[str, Path],
    voice: AgentVoiceConfig | None = None,
    patterns: dict[str, bool] | None = None,
    passthroughs: tuple[str, ...] | None = None,
) -> Agent:
    """Instantiate an Agent from already-resolved fields."""
    return Agent(
        name=name,
        system_prompt=system_prompt,
        memory_namespace=memory_namespace,
        llm_config=llm_config,
        permissions=permissions,
        commands=commands,
        commands_enabled=commands_enabled,
        i18n_language=i18n_language,
        smart_routing=smart_routing,
        show_intermediate=show_intermediate,
        show_tool_recap=show_tool_recap,
        workspaces=workspaces,
        voice=voice,
        patterns=patterns or {},
        passthroughs=passthroughs or (),
    )
