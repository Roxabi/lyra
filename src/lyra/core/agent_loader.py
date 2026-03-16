from __future__ import annotations

import json
import logging
import re
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .agent_models import AgentRow

from .agent_config import (
    _MAX_PROMPT_BYTES,
    _VALID_BACKENDS,
    _WORKSPACE_BUILTIN_CONFLICTS,
    Agent,
    AgentSTTConfig,
    AgentTTSConfig,
    Complexity,
    ModelConfig,
    PersonaConfig,
    SmartRoutingConfig,
    _find_agent_dir,
)
from .persona import compose_system_prompt, load_persona

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared sub-pipeline helpers (TOML + DB loading paths both use these)
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


# ---------------------------------------------------------------------------
# Public loaders
# ---------------------------------------------------------------------------


def load_agent_config(  # noqa: C901, PLR0915 — config parsing with many independent TOML sections and validation branches
    name: str,
    agents_dir: Path | None = None,
    personas_dir: Path | None = None,
    instance_overrides: dict | None = None,
) -> Agent:
    """Load an Agent from a TOML config file, merged with instance-level overrides.

    Looks for <agents_dir>/<name>.toml.
    Falls back to ~/.lyra/agents/ then src/lyra/agents/ if agents_dir is not specified.

    instance_overrides comes from config.toml [defaults] merged with
    [agents.<name>], built by _build_agent_overrides() in __main__.
    It provides machine-specific fallbacks for cwd, persona, and workspaces.

    Resolution order (highest to lowest priority):
        agents/<name>.toml value -> instance_overrides -> hardcoded default

    TOML structure:
        [agent]
        memory_namespace = "lyra"
        permissions = []
        persona = "lyra_default"   # optional: loads from vault

        [model]
        backend = "claude-cli"
        model = "claude-sonnet-4-5"
        max_turns = 10
        tools = []

        [prompt]
        system = "..."             # optional: overrides persona composition
    """
    # Primary gate: only safe characters allowed in agent names.
    # Must fire before _find_agent_dir to avoid FS probes on unsanitised input.
    if not re.match(r"^[a-zA-Z0-9_-]+$", name):
        raise ValueError(f"Invalid agent name {name!r}: only [a-zA-Z0-9_-] allowed")

    directory = _find_agent_dir(name, agents_dir)

    path = directory / f"{name}.toml"
    # Secondary gate (defence-in-depth): guard against symlinks or edge cases
    # in path resolution even though the regex above makes traversal impossible.
    if not path.resolve().is_relative_to(directory.resolve()):
        raise ValueError(f"Agent name {name!r} escapes agents directory")
    if not path.exists():
        raise FileNotFoundError(f"Agent config not found: {path}")

    with path.open("rb") as f:
        data = tomllib.load(f)

    overrides = instance_overrides or {}
    agent_section = data.get("agent", {})

    # Validate declared name matches the filename stem (if declared)
    declared_name = agent_section.get("name")
    if declared_name is not None and declared_name != name:
        raise ValueError(
            f"Agent name mismatch: file {name!r} declares name {declared_name!r}"
        )

    model_section = data.get("model", {})
    prompt_section = data.get("prompt", {})

    backend = model_section.get("backend", "claude-cli")
    model = model_section.get("model", "claude-sonnet-4-5")
    _validate_backend_model(backend, model, name)

    cwd = _resolve_cwd(model_section.get("cwd") or overrides.get("cwd"), name)

    model_cfg = ModelConfig(
        backend=backend,
        model=model,
        max_turns=int(model_section.get("max_turns", 10)),
        tools=tuple(model_section.get("tools", [])),
        cwd=cwd,
        skip_permissions=bool(model_section.get("skip_permissions", False)),
    )

    # Persona loading: agent toml wins, then instance_overrides, then None
    persona_name = agent_section.get("persona") or overrides.get("persona")
    persona: PersonaConfig | None = None
    if persona_name:
        persona = load_persona(persona_name, personas_dir)

    system_prompt = prompt_section.get("system", "")
    if not system_prompt and persona:
        system_prompt = compose_system_prompt(persona)

    encoded_prompt = system_prompt.encode()
    if len(encoded_prompt) > _MAX_PROMPT_BYTES:
        raise ValueError(
            f"system_prompt for agent {name!r} exceeds {_MAX_PROMPT_BYTES // 1024}KB "
            f"limit ({len(encoded_prompt)} bytes)"
        )

    commands = _build_commands_from_dict(data.get("commands", {}))

    plugins_section = data.get("plugins", {})
    plugins_enabled: tuple[str, ...] = tuple(plugins_section.get("enabled", []))

    i18n_language = _validate_i18n_language(
        data.get("i18n", {}).get("default_language", "en"), name
    )

    # Smart routing config (#134)
    smart_routing: SmartRoutingConfig | None = None
    sr_section = agent_section.get("smart_routing")
    if sr_section is not None:
        # TOML path validates model format; DB path skips this (no raw strings)
        sr_models = sr_section.get("models", {})
        for level in Complexity:
            model_id = sr_models.get(level.value)
            if model_id and not re.match(r"^[a-zA-Z0-9_.:-]+$", model_id):
                raise ValueError(
                    f"Invalid model {model_id!r} for smart_routing "
                    f"level {level.value!r}: "
                    "only [a-zA-Z0-9_.:-] characters allowed"
                )
        smart_routing = _build_smart_routing_from_dict(sr_section)

    # Workspaces: merge overrides (lower priority) with agent toml (wins per key)
    workspaces_raw: dict = {
        **overrides.get("workspaces", {}),
        **data.get("workspaces", {}),
    }
    workspaces: dict[str, Path] = {}
    for key, raw_path in workspaces_raw.items():
        if not re.match(r"^[a-zA-Z0-9_-]+$", key):
            raise ValueError(
                f"Invalid workspace name {key!r} in agent {name!r}: "
                "only [a-zA-Z0-9_-] allowed"
            )
        if key in _WORKSPACE_BUILTIN_CONFLICTS:
            raise ValueError(
                f"Workspace key {key!r} in agent {name!r} clashes with "
                f"built-in command /{key}"
            )
        resolved = Path(raw_path).expanduser().resolve()
        if not resolved.is_dir():
            raise ValueError(
                f"[workspaces].{key} path {raw_path!r} for agent {name!r} "
                "is not a directory"
            )
        workspaces[key] = resolved

    # Parse [tts] section
    agent_tts: AgentTTSConfig | None = None
    tts_section = data.get("tts")
    if tts_section is not None:
        _tts_int_fields = ("segment_gap", "crossfade", "chunk_size")
        for _field in _tts_int_fields:
            _val = tts_section.get(_field)
            if _val is not None and not isinstance(_val, int):
                raise TypeError(
                    f"[tts].{_field} must be an integer, got {type(_val).__name__!r}"
                )
        agent_tts = _build_tts_from_dict(tts_section)

    # Parse [stt] section
    agent_stt: AgentSTTConfig | None = None
    stt_section = data.get("stt")
    if stt_section is not None:
        _stt_int_fields = ("language_detection_segments",)
        for _field in _stt_int_fields:
            _val = stt_section.get(_field)
            if _val is not None and not isinstance(_val, int):
                raise TypeError(
                    f"[stt].{_field} must be an integer, got {type(_val).__name__!r}"
                )
        agent_stt = _build_stt_from_dict(stt_section)

    return Agent(
        name=name,
        system_prompt=system_prompt,
        memory_namespace=agent_section.get("memory_namespace", name),
        model_config=model_cfg,
        permissions=tuple(agent_section.get("permissions", [])),
        commands=commands,
        plugins_enabled=plugins_enabled,
        persona=persona,
        i18n_language=i18n_language,
        smart_routing=smart_routing,
        show_intermediate=bool(agent_section.get("show_intermediate", False)),
        workspaces=workspaces,
        tts=agent_tts,
        stt=agent_stt,
    )


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
    )

    # Persona: DB row wins, then instance_overrides
    persona_name = row.persona or overrides.get("persona")
    persona: PersonaConfig | None = None
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
    workspaces: dict[str, Path] = {}
    for key, raw_path in workspaces_raw.items():
        if not re.match(r"^[a-zA-Z0-9_-]+$", key):
            log.warning(
                "Agent %r: invalid workspace name %r -- skipping",
                row.name,
                key,
            )
            continue
        if key in _WORKSPACE_BUILTIN_CONFLICTS:
            log.warning(
                "Agent %r: workspace key %r clashes with built-in"
                " command /%s -- skipping",
                row.name,
                key,
                key,
            )
            continue
        resolved_ws = Path(raw_path).expanduser().resolve()
        if not resolved_ws.is_dir():
            log.warning(
                "Agent %r: workspace %r path %r is not a directory -- skipping",
                row.name,
                key,
                raw_path,
            )
            continue
        workspaces[key] = resolved_ws

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

    return Agent(
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
