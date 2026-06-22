"""Adapter-read web agent roster from ``factory-state`` KV.

Key: ``roster.web`` — agent name index for the smoke UI picker.
AgentStore remains the write SSoT on the hub host; KV is the adapter read mirror.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
from typing import NoReturn

from pydantic import ValidationError

from factory.infrastructure.kv.agent_roster import publish_agent_roster
from factory.infrastructure.kv.factory_state import FACTORY_STATE_BUCKET
from roxabi_contracts.state.agent_roster import WEB_ROSTER_KEY, WebAgentRosterDocument

log = logging.getLogger(__name__)

_AGENT_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")

__all__ = ["publish_agent_roster", "seed_web_agent_roster"]


def _fatal_roster_error(detail: str) -> NoReturn:
    sys.exit(
        "No web agents configured — roster missing or invalid in factory-state KV"
        f" ({detail}). Ensure the hub has started and published roster.web,"
        " or seed agents on the hub host."
    )


async def seed_web_agent_roster(
    js: object,
    *,
    timeout: float = 2.0,
) -> list[str]:
    """Read web agent roster from KV — fatal when absent, malformed, or empty."""
    from nats.js.errors import BucketNotFoundError, KeyNotFoundError

    try:
        async with asyncio.timeout(timeout):
            kv = await js.key_value(FACTORY_STATE_BUCKET)  # type: ignore[attr-defined]
            entry = await kv.get(WEB_ROSTER_KEY)
    except BucketNotFoundError:
        _fatal_roster_error("bucket not found")
    except KeyNotFoundError:
        _fatal_roster_error(f"key {WEB_ROSTER_KEY!r} not found")
    except TimeoutError:
        _fatal_roster_error(f"timeout reading {WEB_ROSTER_KEY!r}")

    try:
        doc = WebAgentRosterDocument.model_validate_json(entry.value)
    except (json.JSONDecodeError, TypeError, ValueError, ValidationError):
        _fatal_roster_error(f"malformed JSON at {WEB_ROSTER_KEY!r}")

    if not doc.agents:
        _fatal_roster_error(f"empty agents[] at {WEB_ROSTER_KEY!r}")

    for name in doc.agents:
        if not _AGENT_NAME_RE.fullmatch(name):
            _fatal_roster_error(f"invalid agent name {name!r}")

    log.info("Loaded %d web agent(s) from KV", len(doc.agents))
    return list(doc.agents)