"""Auth DB seeding and bot-auth wiring for standalone Hub.

Extracted from hub_standalone.py for size compliance (#760).
"""

from __future__ import annotations

import logging

from lyra.bootstrap.factory.config import _load_circuit_config
from lyra.bootstrap.wiring.bootstrap_wiring import BotAuthDeps, _build_bot_auths
from lyra.config import (
    DiscordBotConfig,
    TelegramBotConfig,
    load_multibot_config,
)
from lyra.core.auth.authenticator import Authenticator
from lyra.core.circuit_breaker import CircuitRegistry
from lyra.core.stores.bot_store_protocol import BotStoreProtocol
from lyra.infrastructure.stores.auth_store import AuthStore

log = logging.getLogger(__name__)


async def seed_grants_from_bots(
    auth_store: AuthStore,
    bot_store: BotStoreProtocol,
) -> None:
    """Single canonical: read bots from BotStore, seed permanent grants into auth.db."""
    for bot in bot_store.get_all():
        synthetic = {
            "auth": {
                bot.platform: {
                    "owner_users": bot.owner_users,
                    "trusted_users": bot.trusted_users,
                }
            }
        }
        await auth_store.seed_from_config(synthetic, bot.platform)


def build_bot_auths(
    raw_config: dict,
    auth_store: AuthStore,
    bot_store: BotStoreProtocol,
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
        BotAuthDeps(
            bot_store=bot_store,
            tg_multi_cfg=tg_multi_cfg,
            dc_multi_cfg=dc_multi_cfg,
            auth_store=auth_store,
            admin_user_ids=admin_user_ids,
        )
    )
    log.info("Authenticator: %d admin_user_id(s) configured", len(admin_user_ids))

    if not tg_bot_auths and not dc_bot_auths:
        raise ValueError(
            "No adapters configured — add at least one [[telegram.bots]] or"
            " [[discord.bots]] entry and run 'lyra bot init' to seed the bot store"
        )

    return circuit_registry, admin_user_ids, tg_bot_auths, dc_bot_auths
