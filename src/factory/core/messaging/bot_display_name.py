"""Resolve user-facing bot name for message templates."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from factory.core.messaging.message import InboundMessage

log = logging.getLogger(__name__)

_DEFAULT = "factory"


def bot_display_name(msg: InboundMessage | None, hub: Any | None = None) -> str:
    """Return display name for {bot_name} templates.

    Prefers agent persona ``identity.display_name`` when *hub* can resolve the
    inbound binding; falls back to ``msg.bot_id``.
    """
    if msg is None:
        return _DEFAULT
    if hub is not None:
        resolve_binding = getattr(hub, "resolve_binding", None)
        get_agent = getattr(hub, "get_agent", None)
        if callable(resolve_binding) and callable(get_agent):
            binding = resolve_binding(msg)
            if binding is not None:
                agent_name = getattr(binding, "agent_name", None)
                if isinstance(agent_name, str):
                    agent = get_agent(agent_name)
                    if agent is not None:
                        name = _display_name_from_agent(agent, agent_name)
                        if name:
                            return name
    return msg.bot_id or _DEFAULT


def _display_name_from_agent(agent: Any, agent_name: str) -> str | None:
    store = getattr(agent, "_agent_store", None)
    if store is None:
        return None
    try:
        row = store.get(agent_name)
    except Exception:  # noqa: BLE001 — store backends vary; lookup failure → bot_id fallback
        log.debug("bot_display_name: agent store lookup failed", exc_info=True)
        return None
    if row is None or not getattr(row, "persona_json", None):
        return None
    try:
        persona = json.loads(row.persona_json)
    except (json.JSONDecodeError, TypeError):
        return None
    display = persona.get("identity", {}).get("display_name")
    return display if isinstance(display, str) and display.strip() else None
