"""Auth DB seeding and bot-auth wiring for standalone Hub.

Extracted from hub_standalone.py for size compliance (#760).
"""

from __future__ import annotations

import logging

from lyra.bootstrap.bootstrap_wiring import _build_bot_auths
from lyra.bootstrap.config import _load_circuit_config
from lyra.config import (
    DiscordBotConfig,
    TelegramBotConfig,
    load_multibot_config,
)
from lyra.core.authenticator import Authenticator
from lyra.core.circuit_breaker import CircuitRegistry
from lyra.infrastructure.stores.auth_store import AuthStore

log = logging.getLogger(__name__)


async def seed_auth_store(auth_store: AuthStore, raw_config: dict) -> None:
    """Seed per-bot owner/trusted users as permanent grants into the auth store."""
    auth_block: dict = raw_config.get("auth", {})
    for entry in auth_block.get("telegram_bots", []):
        synthetic = {"auth": {"telegram": entry}}
        await auth_store.seed_from_config(synthetic, "telegram")
    for entry in auth_block.get("discord_bots", []):
        synthetic = {"auth": {"discord": entry}}
        await auth_store.seed_from_config(synthetic, "discord")


def build_bot_auths(
    raw_config: dict,
    auth_store: AuthStore,
) -> tuple[
    CircuitRegistry,
    frozenset[str],
    list[tuple[TelegramBotConfig, Authenticator]],
    list[tuple[DiscordBotConfig, Authenticator]],
]:
    """Load circuit config and build per-bot authenticators.

    Returns (circuit_registry, admin_user_ids, tg_bot_auths, dc_bot_auths).
    Raises ValueError on misconfiguration — caller is responsible for sys.exit.
    """
    circuit_registry, admin_user_ids = _load_circuit_config(raw_config)

    tg_multi_cfg, dc_multi_cfg = load_multibot_config(raw_config)

    tg_bot_auths, dc_bot_auths = _build_bot_auths(
        raw_config,
        tg_multi_cfg,
        dc_multi_cfg,
        auth_store,
        admin_user_ids,
    )
    log.info("Authenticator: %d admin_user_id(s) configured", len(admin_user_ids))

    if not tg_bot_auths and not dc_bot_auths:
        raise ValueError(
            "No adapters configured — add at least one [[telegram.bots]] or"
            " [[discord.bots]] entry with a matching [[auth.telegram_bots]] or"
            " [[auth.discord_bots]] section to config.toml"
        )

    return circuit_registry, admin_user_ids, tg_bot_auths, dc_bot_auths
