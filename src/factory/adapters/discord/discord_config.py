"""Discord configuration model and loader.

Re-exports from factory.core.config so existing importers keep working.
The canonical definition lives in core to avoid pulling discord.py into factory.config
(ADR-059 V6).
"""

from __future__ import annotations

from factory.core.config import DiscordConfig, load_discord_config

__all__ = ["DiscordConfig", "load_discord_config"]
