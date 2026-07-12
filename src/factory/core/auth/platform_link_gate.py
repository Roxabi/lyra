"""Platform-link readiness for inbound chat (ADR-103 Blocks 8–9). Pure port."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

__all__ = [
    "GATED_CHAT_PLATFORMS",
    "PlatformLinkChecker",
    "platform_requires_link",
]

# TG/DC product chat requires dual platform link. Web/CLI smoke exempt V1.
GATED_CHAT_PLATFORMS = frozenset({"telegram", "discord"})


@runtime_checkable
class PlatformLinkChecker(Protocol):
    """Port: is this platform principal chat-ready (dual-linked dash user)?"""

    async def is_platform_chat_ready(self, platform_key: str) -> bool: ...


def platform_requires_link(platform: str) -> bool:
    return platform.lower() in GATED_CHAT_PLATFORMS
