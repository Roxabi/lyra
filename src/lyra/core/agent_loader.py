from __future__ import annotations

import logging
import re
import tomllib
from pathlib import Path

from .agent_builder import (
    _assemble_agent,
    _build_commands_from_dict,
    _build_smart_routing_from_dict,
    _build_stt_from_dict,
    _build_tts_from_dict,
    _resolve_cwd,
    _validate_backend_model,
    _validate_i18n_language,
)
from .agent_config import (
    _MAX_PROMPT_BYTES,
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

# Re-export for backward compatibility.
from .agent_db_loader import agent_row_to_config as agent_row_to_config  # noqa: PLC0414
from .persona import compose_system_prompt, load_persona

log = logging.getLogger(__name__)

__all__ = ["load_agent_config", "agent_row_to_config"]


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
        streaming=bool(model_section.get("streaming", False)),
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

    # Reads [plugins].enabled — TOML key kept stable despite Python field rename
    # to avoid collision with [commands] section (custom command metadata).
    plugins_section = data.get("plugins", {})
    commands_enabled: tuple[str, ...] = tuple(plugins_section.get("enabled", []))

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

    # Patterns: configurable rewrite rules (#345)
    patterns_section = data.get("patterns", {})
    patterns: dict[str, bool] = {
        k: bool(v) for k, v in patterns_section.items()
    }

    # Passthroughs: commands forwarded straight to the LLM
    passthroughs: tuple[str, ...] = tuple(data.get("passthroughs", []))

    return _assemble_agent(
        name=name,
        system_prompt=system_prompt,
        memory_namespace=agent_section.get("memory_namespace", name),
        model_config=model_cfg,
        permissions=tuple(agent_section.get("permissions", [])),
        commands=commands,
        commands_enabled=commands_enabled,
        persona=persona,
        i18n_language=i18n_language,
        smart_routing=smart_routing,
        show_intermediate=bool(agent_section.get("show_intermediate", False)),
        workspaces=workspaces,
        tts=agent_tts,
        stt=agent_stt,
        patterns=patterns,
        passthroughs=passthroughs,
    )
