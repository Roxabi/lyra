"""Web agent roster document for ``factory-state`` KV (``roster.web`` key).

Adapter-facing projection only — agent config fields (prompt, tools, backend)
must never appear on the wire.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

WEB_ROSTER_KEY = "roster.web"
_WEB_ROSTER_SCHEMA_VERSION: Literal[1] = 1


class WebAgentRosterDocument(BaseModel):
    """JSON document stored at ``roster.web`` for the smoke adapter picker."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = _WEB_ROSTER_SCHEMA_VERSION
    updated_at: str
    agents: list[str] = Field(default_factory=list)