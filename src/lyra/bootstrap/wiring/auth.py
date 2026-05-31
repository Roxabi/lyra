"""Bot authentication helpers for multibot bootstrap."""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass

from lyra.config import (
    DiscordBotConfig,
    DiscordMultiConfig,
    TelegramBotConfig,
    TelegramMultiConfig,
)
from lyra.core.auth.authenticator import Authenticator, FromBotStoreDeps
from lyra.core.stores.bot_store_protocol import BotStoreProtocol
from lyra.infrastructure.stores.auth_store import AuthStore
from lyra.infrastructure.stores.identity_alias_store import IdentityAliasStore

log = logging.getLogger(__name__)


@dataclass
class BotAuthDeps:
    bot_store: BotStoreProtocol
    tg_multi_cfg: TelegramMultiConfig
    dc_multi_cfg: DiscordMultiConfig
    auth_store: AuthStore
    admin_user_ids: frozenset[str] = frozenset()
    alias_store: IdentityAliasStore | None = None


def _build_bot_auths(
    deps: BotAuthDeps,
) -> tuple[
    list[tuple[TelegramBotConfig, Authenticator]],
    list[tuple[DiscordBotConfig, Authenticator]],
]:
    """Build (bot_cfg, auth) pairs for each platform, skipping bots without auth."""
    tg_bot_auths: list[tuple[TelegramBotConfig, Authenticator]] = []
    dc_bot_auths: list[tuple[DiscordBotConfig, Authenticator]] = []

    try:
        for bot_cfg in deps.tg_multi_cfg.bots:
            auth = Authenticator.from_bot_store(
                FromBotStoreDeps(
                    platform="telegram",
                    bot_id=bot_cfg.bot_id,
                    bot_store=deps.bot_store,
                    store=deps.auth_store,
                    admin_user_ids=deps.admin_user_ids,
                    alias_store=deps.alias_store,
                )
            )
            if auth is None:
                log.warning(
                    "telegram bot_id=%r has no auth config — skipping",
                    bot_cfg.bot_id,
                )
                continue
            tg_bot_auths.append((bot_cfg, auth))

        for bot_cfg in deps.dc_multi_cfg.bots:
            auth = Authenticator.from_bot_store(
                FromBotStoreDeps(
                    platform="discord",
                    bot_id=bot_cfg.bot_id,
                    bot_store=deps.bot_store,
                    store=deps.auth_store,
                    admin_user_ids=deps.admin_user_ids,
                    alias_store=deps.alias_store,
                )
            )
            if auth is None:
                log.warning(
                    "discord bot_id=%r has no auth config — skipping",
                    bot_cfg.bot_id,
                )
                continue
            dc_bot_auths.append((bot_cfg, auth))
    except ValueError as exc:
        sys.exit(str(exc))

    return tg_bot_auths, dc_bot_auths
