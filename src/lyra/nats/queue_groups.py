"""Canonical NATS queue group names for Lyra's roles.

Queue groups enforce load balancing within a role during rolling restarts so
that no message is delivered twice to the same logical consumer. Each constant
or helper defines exactly one role → name mapping.
"""
from __future__ import annotations

#: Hub-side inbound text message subscription (``NatsBus``).
HUB_INBOUND = "hub-inbound"

#: Queue group for TTS worker processes consuming synthesis requests.
TTS_WORKERS = "tts-workers"

#: Queue group for STT worker processes consuming transcription requests.
STT_WORKERS = "stt-workers"


def adapter_outbound(platform: str, bot_id: str) -> str:
    """Return the NATS queue group for an adapter's outbound subscription.

    Each adapter instance for a given ``(platform, bot_id)`` joins this group
    so that during rolling restarts only one process receives each outbound
    message, preventing duplicate sends to users.

    ``platform`` is the lowercase platform name (e.g. ``"telegram"``,
    ``"discord"``). Callers holding a ``Platform`` enum should pass ``.value``.
    """
    return f"adapter-outbound-{platform}-{bot_id}"
