from __future__ import annotations

import logging
import re
from pathlib import Path

from .agent_config import (
    _VALID_BACKENDS,
    _WORKSPACE_BUILTIN_CONFLICTS,
    AgentSTTConfig,
    AgentTTSConfig,
    Complexity,
    SmartRoutingConfig,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared validation helpers (used by both TOML + DB loading paths)
# ---------------------------------------------------------------------------


def _validate_backend_model(backend: str, model: str, agent_name: str) -> None:
    """Shared validation for backend and model values (TOML + DB paths)."""
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
# Shared builder helpers (used by both TOML + DB loading paths)
# ---------------------------------------------------------------------------


def _build_smart_routing_from_dict(sr_data: dict) -> SmartRoutingConfig:
    """Build a SmartRoutingConfig from a parsed dict (shared by TOML + DB paths)."""
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
    """Build an AgentTTSConfig from a parsed dict (shared by TOML + DB paths)."""
    return AgentTTSConfig(
        engine=tts_data.get("engine"),
        voice=tts_data.get("voice"),
        language=tts_data.get("language"),
        accent=tts_data.get("accent"),
        personality=tts_data.get("personality"),
        speed=tts_data.get("speed"),
        emotion=tts_data.get("emotion"),
        segment_gap=tts_data.get("segment_gap"),
        crossfade=tts_data.get("crossfade"),
        chunked=bool(tts_data["chunked"]) if "chunked" in tts_data else None,
        chunk_size=tts_data.get("chunk_size"),
    )


def _build_stt_from_dict(stt_data: dict) -> AgentSTTConfig:
    """Build an AgentSTTConfig from a parsed dict (shared by TOML + DB paths)."""
    return AgentSTTConfig(
        language_detection_threshold=stt_data.get("language_detection_threshold"),
        language_detection_segments=stt_data.get("language_detection_segments"),
        language_fallback=stt_data.get("language_fallback"),
    )


def _build_commands_from_dict(commands_data: dict) -> dict:
    """Build a commands dict from a parsed dict (shared by TOML + DB paths)."""
    from .command_router import CommandConfig  # local: avoids cycle

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
    """Resolve workspace paths, logging warnings and skipping invalid entries.

    Used by the DB loading path (agent_row_to_config).
    """
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
