"""Canonical NATS queue group names for Lyra's roles.

Queue groups enforce load balancing within a role during rolling restarts so
that no message is delivered twice to the same logical consumer. Each constant
or helper defines exactly one role → name mapping.
"""
from __future__ import annotations

from lyra.core.message import Platform

#: Hub-side inbound text message subscription (``NatsBus``).
HUB_INBOUND = "hub-inbound"


def adapter_outbound(platform: Platform, bot_id: str) -> str:
    """Return the NATS queue group for an adapter's outbound subscription.

    Each adapter instance for a given ``(platform, bot_id)`` joins this group
    so that during rolling restarts only one process receives each outbound
    message, preventing duplicate sends to users.
    """
    return f"adapter-outbound-{platform.value}-{bot_id}"
