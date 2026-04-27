"""Discord configuration model and loader.

Re-exports from lyra.core.config so existing importers keep working.
The canonical definition lives in core to avoid pulling discord.py into lyra.config
(ADR-059 V6).
"""

from __future__ import annotations

from lyra.core.config import DiscordConfig, load_discord_config

__all__ = ["DiscordConfig", "load_discord_config"]
