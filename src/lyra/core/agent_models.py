"""Pure data models for agent configuration rows."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

__all__ = [
    "AgentRow",
    "BotAgentMapRow",
    "AgentRuntimeStateRow",
    "VALID_AGENT_STATUSES",
    "_utc_now_iso",
]

#: Valid values for ``AgentRuntimeStateRow.status`` and ``set_runtime_state()``.
VALID_AGENT_STATUSES: frozenset[str] = frozenset({"idle", "active", "error"})


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class AgentRow:
    """One row from the agents table."""

    name: str
    backend: str
    model: str
    max_turns: int | None = None  # None = unlimited (stored as 0 in DB)
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
    show_tool_recap: bool = True  # show 🔧-prefixed tool summary card after tool use
    # #343 — DB-first agent config
    persona_json: str | None = None
    fallback_language: str = "en"
    patterns_json: str | None = None
    passthroughs_json: str | None = None
    source: str = "db"
    created_at: str = field(default_factory=_utc_now_iso)
    updated_at: str = field(default_factory=_utc_now_iso)

    @classmethod
    def from_db_row(cls, row: tuple) -> "AgentRow":  # type: ignore[type-arg]
        """Construct an AgentRow from a raw aiosqlite SELECT tuple (28 columns)."""
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
            _voice_json,  # deprecated — kept in DB but no longer used
            fallback_language,
            patterns_json,
            passthroughs_json,
            show_tool_recap,
        ) = row
        return cls(
            name=name,
            backend=backend,
            model=model,
            max_turns=max_turns or None,  # 0 sentinel in DB → None (unlimited)
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
            show_tool_recap=(
                bool(show_tool_recap) if show_tool_recap is not None else True
            ),
            persona_json=persona_json,
            fallback_language=fallback_language or "en",
            patterns_json=patterns_json,
            passthroughs_json=passthroughs_json,
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
    settings_json: str | None = None
    updated_at: str = field(default_factory=_utc_now_iso)


@dataclass
class AgentRuntimeStateRow:
    """One row from the agent_runtime_state table."""

    agent_name: str
    last_active_at: str | None
    updated_at: str
    pool_count: int
    status: str
