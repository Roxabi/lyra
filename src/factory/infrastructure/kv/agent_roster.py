"""Publish web agent roster into factory-state KV (hub write / adapter read)."""

from __future__ import annotations

import logging
import re

from factory.core.agent.agent_models import AgentRow, _utc_now_iso
from roxabi_contracts.state.agent_roster import WEB_ROSTER_KEY, WebAgentRosterDocument

from .factory_state import open_or_create_kv

log = logging.getLogger(__name__)

_AGENT_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _valid_agent_name(row: AgentRow) -> str | None:
    if not _AGENT_NAME_RE.fullmatch(row.name):
        log.warning(
            "publish_agent_roster: skipping agent with invalid name %r",
            row.name,
        )
        return None
    return row.name


async def publish_agent_roster(js: object, agent_store: object) -> None:
    """Publish ``roster.web`` from AgentStore into factory-state KV."""
    names = sorted(
        name
        for row in agent_store.get_all()  # type: ignore[attr-defined]
        for name in (_valid_agent_name(row),)
        if name is not None
    )
    doc = WebAgentRosterDocument(updated_at=_utc_now_iso(), agents=names)
    kv = await open_or_create_kv(js)
    await kv.put(WEB_ROSTER_KEY, doc.model_dump_json(exclude_none=True).encode())
    log.debug("publish_agent_roster: wrote %d agent(s) to %s", len(names), WEB_ROSTER_KEY)