"""Deprecation helpers for config.toml."""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

_bot_sections_deprecation_warned: bool = False


def warn_deprecated_bot_sections(raw: dict[str, Any]) -> None:
    """Log a one-time DeprecationWarning if any legacy TOML bot section is present.

    The runtime bot roster now comes from BotStore (`lyra bot init`); these four
    sections are seed-only. Detected sections:
    [[telegram.bots]], [[discord.bots]], [[auth.telegram_bots]], [[auth.discord_bots]].
    """
    global _bot_sections_deprecation_warned
    if _bot_sections_deprecation_warned:
        return
    present = [
        name
        for name, value in (
            ("[[telegram.bots]]", (raw.get("telegram") or {}).get("bots")),
            ("[[discord.bots]]", (raw.get("discord") or {}).get("bots")),
            ("[[auth.telegram_bots]]", (raw.get("auth") or {}).get("telegram_bots")),
            ("[[auth.discord_bots]]", (raw.get("auth") or {}).get("discord_bots")),
        )
        if value
    ]
    if not present:
        return
    _bot_sections_deprecation_warned = True
    log.warning(
        "TOML bot sections are deprecated and seed-only: %s. "
        "The runtime roster now comes from BotStore — seed it with `lyra bot init` "
        "and remove these sections from config.toml. "
        "See docs/bot-management.md (Deprecation Timeline).",
        ", ".join(present),
    )
