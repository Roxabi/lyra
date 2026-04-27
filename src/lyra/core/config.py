"""Frozen dataclasses for core component configuration.

Extracted from constructor parameter explosions (#858).
Placed at core/ level to avoid circular imports between hub/, pool/, and commands/.

Also holds pure adapter config dataclasses (TelegramConfig, DiscordConfig) so that
lyra.config can import them without pulling in adapter libs (ADR-059 V6).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from lyra.core.commands.command_config import CommandConfig

_AUTO_THREAD_TRUE = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True)
class HubConfig:
    """Configuration for Hub constructor.

    Groups rate limiting, pool TTL, and inbound bus sizing params.
    """

    rate_limit: int = 20
    rate_window: int = 60
    pool_ttl: float = 604800.0  # 7 days
    max_pools: int = 500  # hard cap on pool count
    debounce_ms: int = 0
    cancel_on_new_message: bool = False
    turn_timeout: float | None = None
    safe_dispatch_timeout: float = 10.0
    staging_maxsize: int = 500
    platform_queue_maxsize: int = 100
    queue_depth_threshold: int = 100
    max_merged_chars: int = 4096


@dataclass(frozen=True)
class PoolConfig:
    """Configuration for Pool constructor.

    Groups debounce, timeout, and history sizing params.
    """

    turn_timeout: float | None = None
    debounce_ms: int = 300  # DEFAULT_DEBOUNCE_MS
    turn_timeout_ceiling: float | None = None
    safe_dispatch_timeout: float = 10.0
    max_merged_chars: int = 4096
    cancel_on_new_message: bool = False


def _default_pattern_configs() -> dict[str, dict]:
    """Lazy-load pattern configs to avoid circular import at module load."""
    from lyra.core.commands.command_patterns import load_pattern_configs

    return load_pattern_configs()


@dataclass(frozen=True)
class RouterConfig:
    """Configuration for CommandRouter constructor.

    Groups builtin commands, workspaces, and pattern configs.
    """

    builtins: dict[str, "CommandConfig"] = field(default_factory=dict)
    workspaces: dict[str, Path] = field(default_factory=dict)
    patterns: dict[str, bool] = field(default_factory=dict)
    pattern_configs: dict[str, dict] = field(default_factory=_default_pattern_configs)
    on_debounce_change: Callable[[int], None] | None = None
    on_cancel_change: Callable[[bool], None] | None = None
    session_driver: Any = None


# ---------------------------------------------------------------------------
# Adapter config dataclasses — pure Pydantic models, no adapter-lib imports.
# Kept here so lyra.config can import them without pulling in discord.py or
# python-telegram-bot (ADR-059 V6).
# ---------------------------------------------------------------------------


class TelegramConfig(BaseModel):
    """Configuration for the Telegram adapter (credentials resolved at bootstrap)."""

    model_config = ConfigDict(frozen=True)

    token: str
    webhook_secret: str


def load_telegram_config() -> TelegramConfig:
    """Load Telegram configuration from environment variables."""
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token:
        raise SystemExit("Missing required env var: TELEGRAM_TOKEN")
    secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET")
    if not secret:
        raise SystemExit("Missing required env var: TELEGRAM_WEBHOOK_SECRET")
    return TelegramConfig(token=token, webhook_secret=secret)


class DiscordConfig(BaseModel):
    """Configuration for the Discord adapter (credentials resolved at bootstrap)."""

    model_config = ConfigDict(frozen=True)

    token: str = Field(repr=False)
    auto_thread: bool = True


def load_discord_config() -> DiscordConfig:
    """Load Discord configuration from environment variables.

    Raises SystemExit if DISCORD_TOKEN is absent. Never logs the token.
    """
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise SystemExit("Missing required env var: DISCORD_TOKEN")
    auto_thread_str = os.environ.get("DISCORD_AUTO_THREAD", "").strip().lower()
    auto_thread = auto_thread_str in _AUTO_THREAD_TRUE if auto_thread_str else True
    return DiscordConfig(token=token, auto_thread=auto_thread)
