"""Discord adapter package.

Public API: DiscordAdapter is re-exported for backward compatibility.
Internal exports: _discord_typing_worker (for tests).
"""

from __future__ import annotations

from lyra.adapters.discord.adapter import DiscordAdapter
from lyra.adapters.discord_outbound import _discord_typing_worker

__all__ = ["DiscordAdapter", "_discord_typing_worker"]
