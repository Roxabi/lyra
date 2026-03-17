"""Pure data models for agent configuration rows."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

__all__ = ["AgentRow", "BotAgentMapRow", "AgentRuntimeStateRow", "_utc_now_iso"]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class AgentRow:
    """One row from the agents table."""

    name: str
    backend: str
    model: str
    max_turns: int = 10
    tools_json: str = "[]"
    persona: str | None = None
    show_intermediate: bool = False
    smart_routing_json: str | None = None
    plugins_json: str = "[]"
    memory_namespace: str | None = None
    cwd: str | None = None
    tts_json: str | None = None
    stt_json: str | None = None
    skip_permissions: bool = False
    permissions_json: str = "[]"
    workspaces_json: str | None = None
    i18n_language: str = "en"
    commands_json: str | None = None
    streaming: bool = False
    # #343 — DB-first agent config
    persona_json: str | None = None
    voice_json: str | None = None
    fallback_language: str = "en"
    patterns_json: str | None = None
    source: str = "db"
    created_at: str = field(default_factory=_utc_now_iso)
    updated_at: str = field(default_factory=_utc_now_iso)

    @classmethod
    def from_db_row(cls, row: tuple) -> "AgentRow":  # type: ignore[type-arg]
        """Construct an AgentRow from a raw aiosqlite SELECT tuple (26 columns)."""
        (
            name,
            backend,
            model,
            max_turns,
            tools_json,
            persona,
            show_intermediate,
            smart_routing_json,
            plugins_json,
            memory_namespace,
            cwd,
            source,
            created_at,
            updated_at,
            tts_json,
            stt_json,
            skip_permissions,
            permissions_json,
            workspaces_json,
            i18n_language,
            commands_json,
            streaming,
            persona_json,
            voice_json,
            fallback_language,
            patterns_json,
        ) = row
        return cls(
            name=name,
            backend=backend,
            model=model,
            max_turns=max_turns,
            tools_json=tools_json,
            persona=persona,
            show_intermediate=bool(show_intermediate),
            smart_routing_json=smart_routing_json,
            plugins_json=plugins_json,
            memory_namespace=memory_namespace,
            cwd=cwd,
            tts_json=tts_json,
            stt_json=stt_json,
            skip_permissions=bool(skip_permissions),
            permissions_json=permissions_json or "[]",
            workspaces_json=workspaces_json,
            i18n_language=i18n_language or "en",
            commands_json=commands_json,
            streaming=bool(streaming),
            persona_json=persona_json,
            voice_json=voice_json,
            fallback_language=fallback_language or "en",
            patterns_json=patterns_json,
            source=source,
            created_at=created_at,
            updated_at=updated_at,
        )


@dataclass
class BotAgentMapRow:
    """One row from the bot_agent_map table."""

    platform: str
    bot_id: str
    agent_name: str
    updated_at: str = field(default_factory=_utc_now_iso)


@dataclass
class AgentRuntimeStateRow:
    """One row from the agent_runtime_state table."""

    agent_name: str
    last_active_at: str | None
    updated_at: str
    pool_count: int
    status: str
