"""Adapter wiring and lifecycle helpers for multibot bootstrap."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, TypeVar

if TYPE_CHECKING:
    from factory.core.ports.blobstore import BlobStorePort

from factory.adapters.discord import DiscordAdapter
from factory.adapters.telegram import TelegramAdapter
from factory.bootstrap import credentials
from factory.bootstrap.wiring.ingest_wiring import wire_ingest
from factory.config import (
    DiscordBotConfig,
    TelegramBotConfig,
)
from factory.core.auth.authenticator import Authenticator
from factory.core.hub import Hub, OutboundDispatcher, RoutingKey
from factory.core.lifecycle.circuit_breaker import CircuitRegistry
from factory.core.messaging.message import Platform
from factory.core.messaging.messages import MessageManager
from factory.core.messaging.tool_display_config import ToolDisplayConfig
from factory.infrastructure.stores.registry.agent_store import AgentStore
from factory.infrastructure.stores.session.thread_store import ThreadStore
from factory.paths import factory_discord_data_dir

log = logging.getLogger(__name__)

_EntryT = TypeVar("_EntryT")


# ---------------------------------------------------------------------------
# DI containers
# ---------------------------------------------------------------------------


@dataclass
class TelegramWiringDeps:
    hub: Hub
    tg_bot_auths: list[tuple[TelegramBotConfig, Authenticator]]
    bot_agent_map: dict[tuple[str, str], str]
    circuit_registry: CircuitRegistry
    msg_manager: MessageManager
    nats_client: Any = None
    tool_display_config: ToolDisplayConfig | None = None
    blob_store: "BlobStorePort | None" = None


@dataclass
class DiscordWiringDeps:
    hub: Hub
    dc_bot_auths: list[tuple[DiscordBotConfig, Authenticator]]
    bot_agent_map: dict[tuple[str, str], str]
    circuit_registry: CircuitRegistry
    msg_manager: MessageManager
    agent_store: AgentStore | None = None
    vault_dir: str | None = None
    nats_client: Any = None
    tool_display_config: ToolDisplayConfig | None = None
    blob_store: "BlobStorePort | None" = None


# ---------------------------------------------------------------------------
# Shared core
# ---------------------------------------------------------------------------


@dataclass
class _AdapterCoreParams:
    """Parameters for _wire_adapters_core — avoids PLR0913 (max 5 args)."""

    platform: Platform
    platform_name: str
    bot_auths: list[tuple[Any, Authenticator]]
    bot_agent_map: dict[tuple[str, str], str]
    hub: Hub
    circuit_registry: CircuitRegistry
    tool_display_config: ToolDisplayConfig | None
    blob_store: "BlobStorePort | None"
    adapter_factory: Callable[[str], Any]
    collect_entry: Callable[[Any, Any, str], Any]
    resolve_identity: bool = True  # Telegram=True (getMe), Discord=False


async def _wire_adapters_core(
    p: _AdapterCoreParams,
) -> tuple[list[Any], list[OutboundDispatcher]]:
    """Shared loop for Telegram and Discord adapter wiring.

    For each (bot_cfg, auth) pair:
      - look up resolved_agent
      - build adapter via *p.adapter_factory(bot_id)*
      - configure tool_display + typing_publisher
      - call wire_ingest
      - register authenticator, adapter, binding, outbound dispatcher on hub

    *p.collect_entry(adapter, bot_cfg, resolved_agent)* is called after the
    common steps; its return value is appended to the entries list so callers
    can accumulate platform-specific tuples.
    """
    entries: list[Any] = []
    dispatchers: list[OutboundDispatcher] = []

    for bot_cfg, auth in p.bot_auths:
        bot_id: str = bot_cfg.bot_id
        resolved_agent = p.bot_agent_map.get((p.platform_name, bot_id))
        if resolved_agent is None:
            log.warning(
                "%s bot_id=%r not in bot_agent_map — skipping adapter",
                p.platform_name,
                bot_id,
            )
            continue

        adapter = p.adapter_factory(bot_id)
        adapter.configure_tool_display(p.tool_display_config)
        adapter.configure_typing_publisher(p.hub._typing_publisher)
        if p.resolve_identity:
            await adapter.resolve_identity()
        wire_ingest(adapter, p.blob_store)

        # C3: Hub is the trust authority — register authenticator here, not on adapter.
        p.hub.register_authenticator(p.platform, bot_id, auth)
        p.hub.register_adapter(p.platform, bot_id, adapter)

        key = RoutingKey(p.platform, bot_id, "*")
        p.hub.register_binding(
            p.platform,
            bot_id,
            "*",
            resolved_agent,
            key.to_pool_id(),
        )

        dispatcher = OutboundDispatcher(
            platform_name=p.platform_name,
            adapter=adapter,
            circuit=p.circuit_registry.get(p.platform_name),
            circuit_registry=p.circuit_registry,
            bot_id=bot_id,
        )
        p.hub.register_outbound_dispatcher(p.platform, bot_id, dispatcher)

        entries.append(p.collect_entry(adapter, bot_cfg, resolved_agent))
        dispatchers.append(dispatcher)
        log.info(
            "Registered %s bot bot_id=%r agent=%r",
            p.platform_name.capitalize(),
            bot_id,
            resolved_agent,
        )

    return entries, dispatchers


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_channel_ids(raw_ids: list[Any], key: str, bot_id: str) -> frozenset[int]:
    """Parse a list of raw channel IDs, warning on invalid entries."""
    valid: list[int] = []
    for ch in raw_ids:
        try:
            valid.append(int(ch))
        except (ValueError, TypeError):
            log.warning(
                "%s: invalid channel id %r for bot %r — skipping",
                key,
                ch,
                bot_id,
            )
    return frozenset(valid)


def _load_watch_channels(agent_store: AgentStore | None, bot_id: str) -> frozenset[int]:
    """Return the watch_channels frozenset for *bot_id* from the agent store."""
    if agent_store is None:
        return frozenset()
    bot_settings = agent_store.get_bot_settings("discord", bot_id)
    return _parse_channel_ids(
        bot_settings.get("watch_channels", []), "watch_channels", bot_id
    )


# ---------------------------------------------------------------------------
# Wiring functions
# ---------------------------------------------------------------------------


async def wire_telegram_adapters(
    deps: TelegramWiringDeps,
) -> tuple[list[TelegramAdapter], list[OutboundDispatcher]]:
    """Wire each Telegram bot: adapter + dispatcher + hub bindings.

    Returns (adapters, dispatchers) lists.
    """

    def _adapter_factory(bot_id: str) -> TelegramAdapter:
        tg_token, tg_webhook_secret = credentials.load_bot_token("telegram", bot_id)
        return TelegramAdapter(
            bot_id=bot_id,
            token=tg_token,
            inbound_bus=deps.hub.inbound_bus,
            webhook_secret=tg_webhook_secret or "",
            circuit_registry=deps.circuit_registry,
            msg_manager=deps.msg_manager,
            blob_store=deps.blob_store,
        )

    def _collect_entry(
        adapter: TelegramAdapter, bot_cfg: TelegramBotConfig, resolved_agent: str
    ) -> TelegramAdapter:
        return adapter

    return await _wire_adapters_core(
        _AdapterCoreParams(
            platform=Platform.TELEGRAM,
            platform_name="telegram",
            bot_auths=deps.tg_bot_auths,
            bot_agent_map=deps.bot_agent_map,
            hub=deps.hub,
            circuit_registry=deps.circuit_registry,
            tool_display_config=deps.tool_display_config,
            blob_store=deps.blob_store,
            adapter_factory=_adapter_factory,
            collect_entry=_collect_entry,
            resolve_identity=True,
        )
    )


async def wire_discord_adapters(
    deps: DiscordWiringDeps,
) -> tuple[
    list[tuple[DiscordAdapter, DiscordBotConfig, str]],
    list[OutboundDispatcher],
    ThreadStore | None,
]:
    """Wire each Discord bot: adapter + dispatcher + hub bindings.

    Returns (adapters_with_config, dispatchers) where each adapter entry is
    (adapter, bot_cfg, token) — the token is needed later for ``adapter.start()``.
    """
    # Shared ThreadStore for all Discord adapters (#417/S4)
    # One connection to discord.db — shared across all Discord bots.
    # Always use the private discord data dir (factory_discord_data_dir honours
    # $ROXABI_FACTORY_DISCORD_DIR) so both `factory start` and the standalone
    # discord process write to the same location (#1721 success-criterion 3).
    thread_store: ThreadStore | None = None
    if deps.dc_bot_auths:
        _dc_dir = factory_discord_data_dir()
        _dc_dir.mkdir(parents=True, exist_ok=True)
        thread_store = ThreadStore(db_path=_dc_dir / "discord.db")
        await thread_store.connect()

    def _adapter_factory(bot_id: str) -> DiscordAdapter:
        # Finding #18: use default=None to avoid StopIteration on missing bot_id
        _bot_cfg = next(
            (cfg for cfg, _ in deps.dc_bot_auths if cfg.bot_id == bot_id), None
        )
        if _bot_cfg is None:
            raise ValueError(
                f"_adapter_factory: bot_id={bot_id!r} not found in dc_bot_auths"
            )
        bot_cfg = _bot_cfg
        return DiscordAdapter(
            bot_id=bot_id,
            inbound_bus=deps.hub.inbound_bus,
            circuit_registry=deps.circuit_registry,
            msg_manager=deps.msg_manager,
            auto_thread=bot_cfg.auto_thread,
            thread_hot_hours=bot_cfg.thread_hot_hours,
            thread_store=thread_store,
            watch_channels=_load_watch_channels(deps.agent_store, bot_id),
            blob_store=deps.blob_store,
        )

    def _collect_entry(
        adapter: DiscordAdapter, bot_cfg: DiscordBotConfig, resolved_agent: str
    ) -> tuple[DiscordAdapter, DiscordBotConfig, str]:
        # Wire identity resolver for slash command trust (voice commands).
        adapter._resolve_identity_fn = deps.hub.resolve_identity
        dc_token, _ = credentials.load_bot_token("discord", bot_cfg.bot_id)
        return (adapter, bot_cfg, dc_token)

    try:
        entries, dispatchers = await _wire_adapters_core(
            _AdapterCoreParams(
                platform=Platform.DISCORD,
                platform_name="discord",
                bot_auths=deps.dc_bot_auths,
                bot_agent_map=deps.bot_agent_map,
                hub=deps.hub,
                circuit_registry=deps.circuit_registry,
                tool_display_config=deps.tool_display_config,
                blob_store=deps.blob_store,
                adapter_factory=_adapter_factory,
                collect_entry=_collect_entry,
                resolve_identity=False,  # Discord has no getMe equivalent
            )
        )
    except Exception:
        # Close the shared ThreadStore on wiring failure (#417 fix)
        if thread_store is not None:
            await thread_store.close()
        raise

    return entries, dispatchers, thread_store
