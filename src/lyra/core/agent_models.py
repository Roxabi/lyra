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
    source: str = "db"
    created_at: str = field(default_factory=_utc_now_iso)
    updated_at: str = field(default_factory=_utc_now_iso)


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
