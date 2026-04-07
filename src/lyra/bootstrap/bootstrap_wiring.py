"""Adapter wiring and lifecycle helpers for multibot bootstrap."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

from lyra.adapters.discord import DiscordAdapter
from lyra.adapters.telegram import TelegramAdapter
from lyra.config import (
    DiscordBotConfig,
    DiscordMultiConfig,
    TelegramBotConfig,
    TelegramMultiConfig,
)
from lyra.core.authenticator import Authenticator
from lyra.core.circuit_breaker import CircuitRegistry
from lyra.core.hub import Hub, OutboundDispatcher, RoutingKey
from lyra.core.message import Platform
from lyra.core.messages import MessageManager
from lyra.core.stores.agent_store import AgentStore
from lyra.core.stores.auth_store import AuthStore
from lyra.core.stores.credential_store import CredentialStore
from lyra.core.stores.identity_alias_store import IdentityAliasStore
from lyra.core.stores.thread_store import ThreadStore
from lyra.errors import MissingCredentialsError

# Default vault dir for discord.db (#417 / S4)
_DEFAULT_VAULT_DIR = os.path.expanduser("~/.lyra")

log = logging.getLogger(__name__)


async def wire_telegram_adapters(  # noqa: PLR0913 — wiring requires all deps
    hub: Hub,
    tg_bot_auths: list[tuple[TelegramBotConfig, Authenticator]],
    bot_agent_map: dict[tuple[str, str], str],
    cred_store: CredentialStore,
    circuit_registry: CircuitRegistry,
    msg_manager: MessageManager,
    nats_client: Any = None,
) -> tuple[list[TelegramAdapter], list[OutboundDispatcher]]:
    """Wire each Telegram bot: adapter + dispatcher + hub bindings.

    Returns (adapters, dispatchers) lists.
    """
    adapters: list[TelegramAdapter] = []
    dispatchers: list[OutboundDispatcher] = []

    for bot_cfg, auth in tg_bot_auths:
        resolved_agent = bot_agent_map.get(("telegram", bot_cfg.bot_id))
        if resolved_agent is None:
            log.warning(
                "telegram bot_id=%r not in bot_agent_map — skipping adapter",
                bot_cfg.bot_id,
            )
            continue

        tg_creds = await cred_store.get_full("telegram", bot_cfg.bot_id)
        if tg_creds is None:
            raise MissingCredentialsError("telegram", bot_cfg.bot_id)
        tg_token, tg_webhook_secret = tg_creds

        adapter = TelegramAdapter(
            bot_id=bot_cfg.bot_id,
            token=tg_token,
            inbound_bus=hub.inbound_bus,
            webhook_secret=tg_webhook_secret or "",
            circuit_registry=circuit_registry,
            msg_manager=msg_manager,
            turn_store=hub._turn_store,
        )
        await adapter.resolve_identity()
        # C3: Hub is the trust authority — register authenticator here, not on adapter.
        hub.register_authenticator(Platform.TELEGRAM, bot_cfg.bot_id, auth)
        hub.register_adapter(Platform.TELEGRAM, bot_cfg.bot_id, adapter)

        tg_key = RoutingKey(Platform.TELEGRAM, bot_cfg.bot_id, "*")
        hub.register_binding(
            Platform.TELEGRAM,
            bot_cfg.bot_id,
            "*",
            resolved_agent,
            tg_key.to_pool_id(),
        )

        dispatcher = OutboundDispatcher(
            platform_name="telegram",
            adapter=adapter,
            circuit=circuit_registry.get("telegram"),
            circuit_registry=circuit_registry,
            bot_id=bot_cfg.bot_id,
        )
        hub.register_outbound_dispatcher(Platform.TELEGRAM, bot_cfg.bot_id, dispatcher)

        adapters.append(adapter)
        dispatchers.append(dispatcher)
        log.info(
            "Registered Telegram bot bot_id=%r agent=%r",
            bot_cfg.bot_id,
            resolved_agent,
        )

    return adapters, dispatchers


async def wire_discord_adapters(  # noqa: PLR0913, C901 — wiring requires all deps
    hub: Hub,
    dc_bot_auths: list[tuple[DiscordBotConfig, Authenticator]],
    bot_agent_map: dict[tuple[str, str], str],
    cred_store: CredentialStore,
    circuit_registry: CircuitRegistry,
    msg_manager: MessageManager,
    agent_store: AgentStore | None = None,
    vault_dir: str | None = None,
    nats_client: Any = None,
) -> tuple[
    list[tuple[DiscordAdapter, DiscordBotConfig, str]],
    list[OutboundDispatcher],
]:
    """Wire each Discord bot: adapter + dispatcher + hub bindings.

    Returns (adapters_with_config, dispatchers) where each adapter entry is
    (adapter, bot_cfg, token) — the token is needed later for ``adapter.start()``.
    """
    adapters: list[tuple[DiscordAdapter, DiscordBotConfig, str]] = []
    dispatchers: list[OutboundDispatcher] = []

    # Shared ThreadStore for all Discord adapters (#417/S4)
    # One connection to discord.db — shared across bots, closed by the first
    # adapter's close() (all adapters hold the same reference).
    _vault = Path(vault_dir or os.environ.get("LYRA_VAULT_DIR", _DEFAULT_VAULT_DIR))
    thread_store: ThreadStore | None = None
    if dc_bot_auths:
        thread_store = ThreadStore(db_path=_vault / "discord.db")
        await thread_store.connect()

    try:
        for bot_cfg, auth in dc_bot_auths:
            resolved_agent = bot_agent_map.get(("discord", bot_cfg.bot_id))
            if resolved_agent is None:
                log.warning(
                    "discord bot_id=%r not in bot_agent_map — skipping adapter",
                    bot_cfg.bot_id,
                )
                continue

            dc_creds = await cred_store.get_full("discord", bot_cfg.bot_id)
            if dc_creds is None:
                raise MissingCredentialsError("discord", bot_cfg.bot_id)
            dc_token, _ = dc_creds

            watch_channels: frozenset[int] = frozenset()
            if agent_store is not None:
                bot_settings = agent_store.get_bot_settings("discord", bot_cfg.bot_id)

                def _parse_channel_ids(key: str) -> frozenset[int]:
                    raw_ids = bot_settings.get(key, [])
                    valid: list[int] = []
                    for ch in raw_ids:
                        try:
                            valid.append(int(ch))
                        except (ValueError, TypeError):
                            log.warning(
                                "%s: invalid channel id %r for bot %r — skipping",
                                key,
                                ch,
                                bot_cfg.bot_id,
                            )
                    return frozenset(valid)

                watch_channels = _parse_channel_ids("watch_channels")

            adapter = DiscordAdapter(
                bot_id=bot_cfg.bot_id,
                inbound_bus=hub.inbound_bus,
                circuit_registry=circuit_registry,
                msg_manager=msg_manager,
                auto_thread=bot_cfg.auto_thread,
                thread_hot_hours=bot_cfg.thread_hot_hours,
                thread_store=thread_store,
                watch_channels=watch_channels,
                turn_store=hub._turn_store,
            )
            # Wire identity resolver for slash command trust (voice commands).
            adapter._resolve_identity_fn = hub.resolve_identity
            # C3: Hub is the trust authority — register here, not on adapter.
            hub.register_authenticator(Platform.DISCORD, bot_cfg.bot_id, auth)
            hub.register_adapter(Platform.DISCORD, bot_cfg.bot_id, adapter)

            dc_key = RoutingKey(Platform.DISCORD, bot_cfg.bot_id, "*")
            hub.register_binding(
                Platform.DISCORD,
                bot_cfg.bot_id,
                "*",
                resolved_agent,
                dc_key.to_pool_id(),
            )

            dispatcher = OutboundDispatcher(
                platform_name="discord",
                adapter=adapter,
                circuit=circuit_registry.get("discord"),
                circuit_registry=circuit_registry,
                bot_id=bot_cfg.bot_id,
            )
            hub.register_outbound_dispatcher(
                Platform.DISCORD, bot_cfg.bot_id, dispatcher
            )

            adapters.append((adapter, bot_cfg, dc_token))
            dispatchers.append(dispatcher)
            log.info(
                "Registered Discord bot bot_id=%r agent=%r",
                bot_cfg.bot_id,
                resolved_agent,
            )
    except Exception:
        # Close the shared ThreadStore on wiring failure (#417 fix)
        if thread_store is not None:
            await thread_store.close()
        raise

    return adapters, dispatchers


def _build_bot_auths(  # noqa: PLR0913
    raw_config: dict,
    tg_multi_cfg: TelegramMultiConfig,
    dc_multi_cfg: DiscordMultiConfig,
    auth_store: AuthStore,
    admin_user_ids: frozenset[str] = frozenset(),
    alias_store: IdentityAliasStore | None = None,
) -> tuple[
    list[tuple[TelegramBotConfig, Authenticator]],
    list[tuple[DiscordBotConfig, Authenticator]],
]:
    """Build (bot_cfg, auth) pairs for each platform, skipping bots without auth."""
    tg_bot_auths: list[tuple[TelegramBotConfig, Authenticator]] = []
    dc_bot_auths: list[tuple[DiscordBotConfig, Authenticator]] = []

    try:
        for bot_cfg in tg_multi_cfg.bots:
            auth = Authenticator.from_bot_config(
                raw_config,
                "telegram",
                bot_cfg.bot_id,
                store=auth_store,
                admin_user_ids=admin_user_ids,
                alias_store=alias_store,
            )
            if auth is None:
                log.warning(
                    "telegram bot_id=%r has no auth config — skipping",
                    bot_cfg.bot_id,
                )
                continue
            tg_bot_auths.append((bot_cfg, auth))

        for bot_cfg in dc_multi_cfg.bots:
            auth = Authenticator.from_bot_config(
                raw_config,
                "discord",
                bot_cfg.bot_id,
                store=auth_store,
                admin_user_ids=admin_user_ids,
                alias_store=alias_store,
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
