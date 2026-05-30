"""Lyra configuration: multi-bot dataclasses and top-level config loader.

Supports both the new multi-bot schema (preferred):
    [[telegram.bots]]
    bot_id = "lyra"
    token = "env:TELEGRAM_TOKEN"
    ...

And the legacy flat schema (backward compat, treated as single bot with bot_id="main"):
    [auth.telegram]
    default = "blocked"
    ...

Re-exports legacy single-bot dataclasses for callers that still use them.

Deprecated classes
------------------
TelegramMultiConfig, DiscordMultiConfig — TOML-sourced bot roster is seed-only.
The runtime roster comes from BotStore via multibot_config_from_store().
See docs/bot-management.md (Deprecation Timeline).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from pydantic import BaseModel, ConfigDict

from lyra.core.config import (
    DiscordConfig,
    TelegramConfig,
    load_discord_config,
    load_telegram_config,
)
from lyra.core.stores import BotStoreProtocol

# Backward-compat alias: callers using load_config get the Telegram loader.
load_config = load_telegram_config

__all__ = [
    "DiscordConfig",
    "load_discord_config",
    "TelegramConfig",
    "load_config",
    # Multi-bot additions
    "TelegramBotConfig",
    "DiscordBotConfig",
    "TelegramMultiConfig",
    "DiscordMultiConfig",
    "load_multibot_config",
    "multibot_config_from_store",
    # Discord-specific defaults (#1514)
    "DISCORD_DEFAULT_AUTO_THREAD",
    "DISCORD_DEFAULT_THREAD_HOT_HOURS",
]

log = logging.getLogger(__name__)

_ENV_PREFIX = "env:"
_AUTO_THREAD_TRUE = frozenset({"1", "true", "yes", "on"})

# Discord-specific defaults — intentionally diverge from the generic bot_models defaults
# (DEFAULT_AUTO_THREAD=False / DEFAULT_THREAD_HOT_HOURS=24).
# Discord threads conversations by default; the generic store default is False for
# non-threading platforms (e.g. Telegram). See #1514. Keep the divergence deliberate.
DISCORD_DEFAULT_AUTO_THREAD: bool = True
DISCORD_DEFAULT_THREAD_HOT_HOURS: int = 36


def _resolve_value(value: str) -> str:
    """Resolve a config value: if it starts with 'env:', read from environment.

    Example: "env:TELEGRAM_TOKEN" → os.environ["TELEGRAM_TOKEN"]
    Falls back to empty string if the env var is missing (caller validates).
    """
    if value.startswith(_ENV_PREFIX):
        env_var = value[len(_ENV_PREFIX) :]
        resolved = os.environ.get(env_var, "")
        if not resolved:
            log.warning("env var %r referenced in config is not set", env_var)
        return resolved
    return value


class TelegramBotConfig(BaseModel):
    """Configuration for a single Telegram bot instance.

    Credentials (token, webhook_secret) are NOT stored here — they are resolved
    at bootstrap time from /run/secrets/bot_token-<bot_id>.
    """

    model_config = ConfigDict(frozen=True)

    bot_id: str
    agent: str = "lyra_default"


class DiscordBotConfig(BaseModel):
    """Configuration for a single Discord bot instance.

    Credentials (token) are NOT stored here — they are resolved at bootstrap
    time from /run/secrets/bot_token-<bot_id>.
    """

    model_config = ConfigDict(frozen=True)

    bot_id: str
    auto_thread: bool = DISCORD_DEFAULT_AUTO_THREAD
    agent: str = "lyra_default"
    thread_hot_hours: int = DISCORD_DEFAULT_THREAD_HOT_HOURS


class TelegramMultiConfig(BaseModel):
    """Parsed [telegram] section: list of bot configs.

    .. deprecated::
        TOML-sourced bot roster is seed-only; runtime roster comes from BotStore
        via multibot_config_from_store(). See docs/bot-management.md.
    """

    bots: list[TelegramBotConfig] = []


class DiscordMultiConfig(BaseModel):
    """Parsed [discord] section: list of bot configs.

    .. deprecated::
        TOML-sourced bot roster is seed-only; runtime roster comes from BotStore
        via multibot_config_from_store(). See docs/bot-management.md.
    """

    bots: list[DiscordBotConfig] = []


def _parse_telegram_bots(raw: dict[str, Any]) -> list[TelegramBotConfig]:
    """Parse [[telegram.bots]] array from raw config.

    Each entry requires: bot_id.
    Optional: agent (default "lyra_default").

    Credentials (token, webhook_secret) are resolved at bootstrap time from
    /run/secrets/bot_token-<bot_id> and are not read here.
    """
    tg_section: dict = raw.get("telegram", {})
    bots_raw: list[dict] = tg_section.get("bots", [])

    bots: list[TelegramBotConfig] = []
    for entry in bots_raw:
        bot_id: str = entry.get("bot_id", "main")
        agent: str = entry.get("agent", "lyra_default")

        bots.append(
            TelegramBotConfig(
                bot_id=bot_id,
                agent=agent,
            )
        )
    return bots


def _parse_discord_bots(raw: dict[str, Any]) -> list[DiscordBotConfig]:
    """Parse [[discord.bots]] array from raw config.

    Each entry requires: bot_id.
    Optional: auto_thread (default DISCORD_DEFAULT_AUTO_THREAD),
    agent (default "lyra_default").

    Credentials (token) are resolved at bootstrap time from
    /run/secrets/bot_token-<bot_id> and are not read here.
    """
    dc_section: dict = raw.get("discord", {})
    bots_raw: list[dict] = dc_section.get("bots", [])

    bots: list[DiscordBotConfig] = []
    for entry in bots_raw:
        bot_id: str = entry.get("bot_id", "main")
        auto_thread: bool = entry.get("auto_thread", DISCORD_DEFAULT_AUTO_THREAD)
        agent: str = entry.get("agent", "lyra_default")
        thread_hot_hours: int = int(
            entry.get("thread_hot_hours", DISCORD_DEFAULT_THREAD_HOT_HOURS)
        )

        bots.append(
            DiscordBotConfig(
                bot_id=bot_id,
                auto_thread=auto_thread,
                agent=agent,
                thread_hot_hours=thread_hot_hours,
            )
        )
    return bots


def load_multibot_config(
    raw: dict[str, Any],
) -> tuple[TelegramMultiConfig, DiscordMultiConfig]:
    """Parse multi-bot config from a raw TOML dict.

    Reads [[telegram.bots]] and [[discord.bots]] arrays.

    Backward compatibility: if no [[telegram.bots]] array is present but
    [auth.telegram] exists, the legacy single-bot env-var loader is used
    to synthesize a single TelegramBotConfig with bot_id="main".
    Same for Discord.

    Returns (TelegramMultiConfig, DiscordMultiConfig) — both may have empty
    bot lists if neither new-style nor legacy config is found.
    """
    tg_bots = _parse_telegram_bots(raw)
    dc_bots = _parse_discord_bots(raw)

    # Backward compat: no [[telegram.bots]] key declared but [auth.telegram] is present
    # → synthesize a single TelegramBotConfig with bot_id="main" (legacy path).
    # Use "bots" not in telegram section (not top-level key) to distinguish
    # "no bots declared" from "bots declared but all failed resolution".
    # Credentials are NOT stored here — resolved at bootstrap from /run/secrets.
    tg_has_bots_key = "bots" in raw.get("telegram", {})
    if not tg_bots and not tg_has_bots_key and raw.get("auth", {}).get("telegram"):
        log.info(
            "telegram: no [[telegram.bots]] found; "
            "falling back to legacy single-bot path (bot_id='main')"
        )
        tg_bots = [
            TelegramBotConfig(
                bot_id="main",
                agent="lyra_default",
            )
        ]

    # Backward compat: no [[discord.bots]] key declared but [auth.discord] is present.
    dc_has_bots_key = "bots" in raw.get("discord", {})
    if not dc_bots and not dc_has_bots_key and raw.get("auth", {}).get("discord"):
        auto_thread_str = os.environ.get("DISCORD_AUTO_THREAD", "").strip().lower()
        auto_thread = (
            auto_thread_str in _AUTO_THREAD_TRUE
            if auto_thread_str
            else DISCORD_DEFAULT_AUTO_THREAD
        )
        log.info(
            "discord: no [[discord.bots]] found; "
            "falling back to legacy single-bot path (bot_id='main')"
        )
        dc_bots = [
            DiscordBotConfig(
                bot_id="main",
                auto_thread=auto_thread,
                agent="lyra_default",
            )
        ]

    return TelegramMultiConfig(bots=tg_bots), DiscordMultiConfig(bots=dc_bots)


def multibot_config_from_store(
    bot_store: BotStoreProtocol,
) -> tuple[TelegramMultiConfig, DiscordMultiConfig]:
    """Build the runtime bot roster from BotStore (SSoT), not TOML.

    Partitions bot_store.get_all() by platform. Telegram rows become
    TelegramBotConfig(bot_id, agent); Discord rows become
    DiscordBotConfig(bot_id, auto_thread, agent, thread_hot_hours).
    """
    tg_bots: list[TelegramBotConfig] = []
    dc_bots: list[DiscordBotConfig] = []
    for row in bot_store.get_all():
        if row.platform == "telegram":
            tg_bots.append(TelegramBotConfig(bot_id=row.bot_id, agent=row.agent))
        elif row.platform == "discord":
            dc_bots.append(
                DiscordBotConfig(
                    bot_id=row.bot_id,
                    auto_thread=row.auto_thread,
                    agent=row.agent,
                    thread_hot_hours=row.thread_hot_hours,
                )
            )
    return TelegramMultiConfig(bots=tg_bots), DiscordMultiConfig(bots=dc_bots)
