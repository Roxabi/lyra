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
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

from lyra.adapters.discord import DiscordConfig, load_discord_config
from lyra.adapters.telegram import TelegramConfig, load_config

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
]

log = logging.getLogger(__name__)

_ENV_PREFIX = "env:"
_AUTO_THREAD_TRUE = frozenset({"1", "true", "yes", "on"})


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


@dataclass(frozen=True)
class TelegramBotConfig:
    """Configuration for a single Telegram bot instance."""

    bot_id: str
    token: str = field(repr=False)
    bot_username: str
    webhook_secret: str = field(repr=False)
    agent: str = "lyra_default"


@dataclass(frozen=True)
class DiscordBotConfig:
    """Configuration for a single Discord bot instance."""

    bot_id: str
    token: str = field(repr=False)
    auto_thread: bool = True
    agent: str = "lyra_default"


@dataclass
class TelegramMultiConfig:
    """Parsed [telegram] section: list of bot configs."""

    bots: list[TelegramBotConfig] = field(default_factory=list)


@dataclass
class DiscordMultiConfig:
    """Parsed [discord] section: list of bot configs."""

    bots: list[DiscordBotConfig] = field(default_factory=list)


def _parse_telegram_bots(raw: dict[str, Any]) -> list[TelegramBotConfig]:
    """Parse [[telegram.bots]] array from raw config.

    Each entry requires: bot_id, token, webhook_secret.
    Optional: bot_username (default "lyra_bot"), agent (default "lyra_default").

    Supports "env:VAR_NAME" syntax for token/webhook_secret/bot_username.
    """
    tg_section: dict = raw.get("telegram", {})
    bots_raw: list[dict] = tg_section.get("bots", [])

    bots: list[TelegramBotConfig] = []
    for entry in bots_raw:
        bot_id: str = entry.get("bot_id", "main")
        raw_token: str = entry.get("token", "")
        raw_secret: str = entry.get("webhook_secret", "")
        raw_username: str = entry.get("bot_username", "env:TELEGRAM_BOT_USERNAME")
        agent: str = entry.get("agent", "lyra_default")

        token = _resolve_value(raw_token)
        webhook_secret = _resolve_value(raw_secret)
        bot_username = _resolve_value(raw_username) or "lyra_bot"

        if not token:
            raise ValueError(
                f"telegram bot_id={bot_id!r}: token is empty after resolution"
                " — check that the env var referenced in 'token' is set"
            )
        if not webhook_secret:
            raise ValueError(
                f"telegram bot_id={bot_id!r}: webhook_secret is empty — "
                "set the env var referenced in 'webhook_secret' before starting"
            )

        bots.append(
            TelegramBotConfig(
                bot_id=bot_id,
                token=token,
                bot_username=bot_username,
                webhook_secret=webhook_secret,
                agent=agent,
            )
        )
    return bots


def _parse_discord_bots(raw: dict[str, Any]) -> list[DiscordBotConfig]:
    """Parse [[discord.bots]] array from raw config.

    Each entry requires: bot_id, token.
    Optional: auto_thread (default True), agent (default "lyra_default").

    Supports "env:VAR_NAME" syntax for token.
    """
    dc_section: dict = raw.get("discord", {})
    bots_raw: list[dict] = dc_section.get("bots", [])

    bots: list[DiscordBotConfig] = []
    for entry in bots_raw:
        bot_id: str = entry.get("bot_id", "main")
        raw_token: str = entry.get("token", "")
        auto_thread: bool = entry.get("auto_thread", True)
        agent: str = entry.get("agent", "lyra_default")

        token = _resolve_value(raw_token)

        if not token:
            raise ValueError(
                f"discord bot_id={bot_id!r}: token is empty after resolution"
                " — check that the env var referenced in 'token' is set"
            )

        bots.append(
            DiscordBotConfig(
                bot_id=bot_id,
                token=token,
                auto_thread=auto_thread,
                agent=agent,
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
    # → synthesize from env vars (legacy path).
    # Use "bots" not in telegram section (not top-level key) to distinguish
    # "no bots declared" from "bots declared but all failed resolution".
    tg_has_bots_key = "bots" in raw.get("telegram", {})
    if not tg_bots and not tg_has_bots_key and raw.get("auth", {}).get("telegram"):
        token = os.environ.get("TELEGRAM_TOKEN", "")
        webhook_secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
        bot_username = os.environ.get("TELEGRAM_BOT_USERNAME", "lyra_bot")
        if token:
            log.info(
                "telegram: no [[telegram.bots]] found; "
                "falling back to legacy TELEGRAM_TOKEN env var (bot_id='main')"
            )
            tg_bots = [
                TelegramBotConfig(
                    bot_id="main",
                    token=token,
                    bot_username=bot_username,
                    webhook_secret=webhook_secret,
                    agent="lyra_default",
                )
            ]

    # Backward compat: no [[discord.bots]] key declared but [auth.discord] is present.
    dc_has_bots_key = "bots" in raw.get("discord", {})
    if not dc_bots and not dc_has_bots_key and raw.get("auth", {}).get("discord"):
        token = os.environ.get("DISCORD_TOKEN", "")
        if token:
            auto_thread_str = os.environ.get("DISCORD_AUTO_THREAD", "").strip().lower()
            auto_thread = (
                auto_thread_str in _AUTO_THREAD_TRUE if auto_thread_str else True
            )
            log.info(
                "discord: no [[discord.bots]] found; "
                "falling back to legacy DISCORD_TOKEN env var (bot_id='main')"
            )
            dc_bots = [
                DiscordBotConfig(
                    bot_id="main",
                    token=token,
                    auto_thread=auto_thread,
                    agent="lyra_default",
                )
            ]

    return TelegramMultiConfig(bots=tg_bots), DiscordMultiConfig(bots=dc_bots)
