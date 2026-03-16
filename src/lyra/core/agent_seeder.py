"""TOML seeder: parse agent TOML files into AgentRow for DB import."""

from __future__ import annotations

import json
import logging
import re
import tomllib
from pathlib import Path
from typing import Protocol

from .agent_models import AgentRow

log = logging.getLogger(__name__)

__all__ = ["seed_from_toml"]

_VALID_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


class AgentStoreProtocol(Protocol):
    """Structural interface for the subset of AgentStore used by the seeder."""

    def get(self, name: str) -> AgentRow | None: ...
    async def upsert(self, row: AgentRow) -> None: ...


async def seed_from_toml(
    store: AgentStoreProtocol,
    path: Path,
    *,
    force: bool = False,
) -> int:
    """Import agent from TOML into *store*. Returns 1 if imported, 0 if skipped/error.

    Skips if agent already exists in the store cache (unless *force* is True).
    """
    row = _parse_toml(path)
    if row is None:
        return 0

    if not force and store.get(row.name) is not None:
        return 0

    await store.upsert(row)
    return 1


def _parse_toml(path: Path) -> AgentRow | None:
    """Parse an agent TOML file and return an :class:`AgentRow`, or *None* on error."""
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except (tomllib.TOMLDecodeError, OSError) as exc:
        log.warning("seed_from_toml: failed to parse %s: %s", path, exc)
        return None

    agent_section = data.get("agent", {})
    model_section = data.get("model", {})

    name = agent_section.get("name") or model_section.get("name")
    if not name:
        log.warning("seed_from_toml: no [agent].name in %s — skipped", path)
        return None
    if not _VALID_NAME_RE.match(name):
        log.warning("seed_from_toml: invalid agent name %r in %s — skipped", name, path)
        return None

    # Fields may live under [model] (wizard-generated) or [agent] (legacy).
    def _m(key: str, default=None):  # type: ignore[no-untyped-def]
        return model_section.get(key) or agent_section.get(key, default)

    backend = _m("backend", "anthropic-sdk")
    model = _m("model", "claude-3-5-haiku-20241022")
    max_turns = _m("max_turns", 10)
    tools_json = json.dumps(_m("tools", []))
    persona = agent_section.get("persona")
    show_intermediate = agent_section.get("show_intermediate", False)
    smart_routing = agent_section.get("smart_routing")
    smart_routing_json = json.dumps(smart_routing) if smart_routing else None
    # plugins may live under [plugins].enabled (wizard) or [agent].plugins (legacy)
    plugins_json = json.dumps(
        data.get("plugins", {}).get("enabled") or agent_section.get("plugins", [])
    )
    memory_namespace = agent_section.get("memory_namespace")
    cwd = _m("cwd")
    skip_permissions = bool(_m("skip_permissions", False))

    # Serialize [tts] and [stt] sections to JSON (None if section absent)
    tts_section = data.get("tts")
    tts_json = json.dumps(tts_section) if tts_section is not None else None
    stt_section = data.get("stt")
    stt_json = json.dumps(stt_section) if stt_section is not None else None

    # New fields: permissions, workspaces, i18n, commands
    permissions_json = json.dumps(agent_section.get("permissions", []))
    workspaces_section = data.get("workspaces")
    workspaces_json = (
        json.dumps(workspaces_section) if workspaces_section is not None else None
    )
    i18n_section = data.get("i18n", {})
    i18n_language = i18n_section.get("default_language", "en")
    commands_section = data.get("commands")
    commands_json = (
        json.dumps(commands_section) if commands_section is not None else None
    )

    return AgentRow(
        name=name,
        backend=backend,
        model=model,
        max_turns=max_turns,
        tools_json=tools_json,
        persona=persona,
        show_intermediate=show_intermediate,
        smart_routing_json=smart_routing_json,
        plugins_json=plugins_json,
        memory_namespace=memory_namespace,
        cwd=cwd,
        tts_json=tts_json,
        stt_json=stt_json,
        skip_permissions=skip_permissions,
        permissions_json=permissions_json,
        workspaces_json=workspaces_json,
        i18n_language=i18n_language,
        commands_json=commands_json,
        source="toml-seed",
    )
