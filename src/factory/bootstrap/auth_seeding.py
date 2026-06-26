"""Auth DB seeding and bot-auth wiring for standalone Hub.

Extracted from hub_standalone.py for size compliance (#760).
"""

from __future__ import annotations

import logging

from factory.bootstrap.factory.config import _load_circuit_config
from factory.bootstrap.wiring.auth import BotAuthDeps, _build_bot_auths
from factory.config import (
    DiscordBotConfig,
    DiscordMultiConfig,
    TelegramBotConfig,
    TelegramMultiConfig,
    multibot_config_from_store,
)
from factory.core.auth.agent_grants import Capability, Principal, PrincipalKind
from factory.core.auth.authenticator import Authenticator
from factory.core.auth.platform_keys import format_platform_key, is_platform_key
from factory.core.lifecycle.circuit_breaker import CircuitRegistry
from factory.core.stores.bot_store_protocol import BotStoreProtocol
from factory.infrastructure.stores.identity.agent_grant_store import AgentGrantStore
from factory.infrastructure.stores.identity.identity_alias_store import (
    IdentityAliasStore,
)
from factory.infrastructure.stores.identity.user_store import UserStore

log = logging.getLogger(__name__)


def _platform_key(uid: str, platform: str) -> str:
    """Normalize a bare or prefixed user id to a canonical platform key."""
    if is_platform_key(uid):
        return uid
    return format_platform_key(platform, uid)


def merge_owner_admin_ids(
    bot_store: BotStoreProtocol,
    admin_user_ids: frozenset[str],
) -> frozenset[str]:
    """Merge bot owner_users into admin_user_ids (platform-prefixed)."""
    merged = set(admin_user_ids)
    for bot in bot_store.get_all():
        for uid in bot.owner_users:
            merged.add(_platform_key(uid, bot.platform))
    return frozenset(merged)


async def ensure_users_from_bots(
    user_store: UserStore,
    bot_store: BotStoreProtocol,
    *,
    admin_user_ids: frozenset[str] = frozenset(),
) -> None:
    """Register canonical users for every configured owner/trusted/admin identity."""
    keys: set[str] = set(admin_user_ids)
    for bot in bot_store.get_all():
        for uid in bot.owner_users:
            keys.add(_platform_key(uid, bot.platform))
        for uid in bot.trusted_users:
            keys.add(_platform_key(uid, bot.platform))
    await user_store.ensure_from_platform_keys(sorted(keys))


async def seed_agent_grants_from_bots(
    user_store: UserStore,
    grant_store: AgentGrantStore,
    bot_store: BotStoreProtocol,
    *,
    admin_user_ids: frozenset[str] = frozenset(),
) -> None:
    """Register users and seed agent ``use`` grants on canonical ``rx:user:`` ids."""
    await ensure_users_from_bots(
        user_store, bot_store, admin_user_ids=admin_user_ids
    )

    for bot in bot_store.get_all():
        platform_keys: set[str] = set()
        for uid in bot.owner_users:
            platform_keys.add(_platform_key(uid, bot.platform))
        for uid in bot.trusted_users:
            platform_keys.add(_platform_key(uid, bot.platform))
        prefix = "tg:user:" if bot.platform == "telegram" else "dc:user:"
        for aid in admin_user_ids:
            if aid.startswith(prefix):
                platform_keys.add(aid)

        for key in sorted(platform_keys):
            rx_user = await user_store.ensure_user(key)
            await grant_store.grant(
                bot.agent,
                Principal(kind=PrincipalKind.USER, id=rx_user),
                capability=Capability.USE,
                granted_by="bootstrap",
                source="bot_store",
            )


async def seed_identity_and_grants(
    grant_store: AgentGrantStore,
    bot_store: BotStoreProtocol,
    user_store: UserStore | None,
    *,
    admin_user_ids: frozenset[str] = frozenset(),
) -> None:
    """Register canonical users and seed agent grants from bot roster."""
    if user_store is None:
        log.warning("UserStore unavailable — skipping identity seed")
        return
    await seed_agent_grants_from_bots(
        user_store,
        grant_store,
        bot_store,
        admin_user_ids=admin_user_ids,
    )


def build_bot_auths(
    raw_config: dict,
    auth_store: object,
    bot_store: BotStoreProtocol,
    alias_store: IdentityAliasStore | None = None,
) -> tuple[
    CircuitRegistry,
    frozenset[str],
    list[tuple[TelegramBotConfig, Authenticator]],
    list[tuple[DiscordBotConfig, Authenticator]],
]:
    """Load circuit config and build per-bot authenticators.

    Returns (circuit_registry, admin_user_ids, tg_bot_auths, dc_bot_auths).
    Raises ValueError on misconfiguration — caller is responsible for sys.exit.

    ``auth_store`` is still wired for BLOCKED lookups; OWNER/TRUSTED seeding
    is replaced by AgentGrantStore (ADR-090).
    """
    circuit_registry, admin_user_ids = _load_circuit_config(raw_config)
    admin_user_ids = merge_owner_admin_ids(bot_store, admin_user_ids)

    tg_multi_cfg, dc_multi_cfg = multibot_config_from_store(bot_store)

    tg_bot_auths, dc_bot_auths = _build_bot_auths(
        BotAuthDeps(
            bot_store=bot_store,
            tg_multi_cfg=tg_multi_cfg,
            dc_multi_cfg=dc_multi_cfg,
            auth_store=auth_store,  # type: ignore[arg-type]
            admin_user_ids=admin_user_ids,
            alias_store=alias_store,
        )
    )
    log.info("Authenticator: %d admin_user_id(s) configured", len(admin_user_ids))

    if not tg_bot_auths and not dc_bot_auths:
        raise ValueError(
            "No bots configured — the runtime roster is sourced from BotStore."
            " Run 'lyra bot init' to seed it from config.toml,"
            " then 'lyra bot list' to verify."
        )

    return circuit_registry, admin_user_ids, tg_bot_auths, dc_bot_auths