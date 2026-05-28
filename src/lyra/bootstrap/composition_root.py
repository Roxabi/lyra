"""Composition root for bootstrap construction logic.

Extracts construction (not runtime) from standalone bootstraps into
type-based DI composition functions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any

from lyra.adapters.clipool.clipool_worker import CliPoolNatsWorker
from lyra.adapters.nats.nats_outbound_listener import NatsOutboundListener
from lyra.bootstrap import credentials
from lyra.bootstrap.auth_seeding import build_bot_auths, seed_grants_from_bots
from lyra.bootstrap.bootstrap_stores import StoreBundle
from lyra.bootstrap.factory.agent_factory import _resolve_bot_agent_map
from lyra.bootstrap.factory.config import (
    MessageIndexConfig,
    _load_cli_pool_config,
    _load_messages,
    build_adapter_config_bundle,
)
from lyra.bootstrap.factory.hub_builder import (
    build_hub,
    build_inbound_bus,
    build_llm_client,
    register_agents,
)
from lyra.bootstrap.factory.llm_overlay import init_nats_llm
from lyra.bootstrap.factory.voice_overlay import init_nats_stt, init_nats_tts
from lyra.bootstrap.lifecycle.lifecycle_helpers import close_safely
from lyra.bootstrap.standalone.hub_standalone_helpers import (
    build_pairing_manager,
    load_agent_configs,
)
from lyra.bootstrap.wiring.nats_wiring import (
    NatsDcWiringDeps,
    NatsTgWiringDeps,
    wire_nats_discord_proxies,
    wire_nats_telegram_proxies,
)
from lyra.core.cli.cli_pool import CliPool
from lyra.core.messaging.message import InboundMessage, Platform
from lyra.infrastructure.audit import JetStreamAuditSink
from lyra.infrastructure.resume_publisher_adapter import TurnPublisherAdapter
from lyra.infrastructure.stores.agent_store import AgentStore
from lyra.infrastructure.stores.thread_store import ThreadStore
from lyra.infrastructure.stores.turn_store import TurnStore
from lyra.nats.nats_bus import NatsBus
from lyra.nats.queue_groups import adapter_outbound
from lyra.transport.turn_publisher import TurnPublisher
from lyra.transport.typing_publisher import TypingPublisher

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Minimal type-based DI container
# ---------------------------------------------------------------------------


class DependencyGraph:
    """Minimal type-based DI container."""

    def __init__(self) -> None:
        self._registry: dict[type, Any] = {}

    def register(self, iface: type, instance: Any) -> None:
        self._registry[iface] = instance

    def resolve(self, iface: type) -> Any:
        return self._registry[iface]


# ---------------------------------------------------------------------------
# Subsystem dataclasses
# ---------------------------------------------------------------------------


@dataclass
class HubSubsystem:
    """All objects constructed for the Hub process."""

    hub: Any
    inbound_bus: Any
    inbound_bus_cfg: Any
    tg_proxies: list[Any] = field(default_factory=list)
    dc_proxies: list[Any] = field(default_factory=list)
    tg_dispatchers: list[Any] = field(default_factory=list)
    dc_dispatchers: list[Any] = field(default_factory=list)
    stt_service: Any = None
    tts_service: Any = None
    nats_llm_client: Any = None
    cli_nats_driver: Any = None
    audit_sink: Any = None
    typing_publisher: Any = None
    turn_publisher: Any = None
    pairing_manager: Any = None
    circuit_registry: Any = None
    admin_user_ids: frozenset[str] = field(default_factory=frozenset)
    tg_bot_auths: list[Any] = field(default_factory=list)
    dc_bot_auths: list[Any] = field(default_factory=list)
    bot_agent_map: dict[str, str] = field(default_factory=dict)
    agent_configs: dict[str, Any] = field(default_factory=dict)
    msg_manager: Any = None


@dataclass
class AdapterEntry:
    """Single adapter + its bus + typing listener."""

    adapter: Any
    token: str | None
    inbound_bus: Any
    typing_listener: Any
    bot_id: str


@dataclass
class AdapterSubsystem:
    """All objects constructed for an adapter process."""

    entries: list[AdapterEntry] = field(default_factory=list)
    turn_store: Any = None
    thread_store: Any = None
    config_bundle: Any = None


@dataclass
class ClipoolSubsystem:
    """All objects constructed for the CliPool worker process."""

    cli_pool: Any = None
    worker: Any = None
    cli_pool_cfg: Any = None


# ---------------------------------------------------------------------------
# Composition functions
# ---------------------------------------------------------------------------


async def compose_hub(
    raw_config: dict,
    nc: Any,
    vault_dir: Path,
    deps: DependencyGraph,
) -> HubSubsystem:
    """Construct the Hub subsystem (no runtime lifecycle)."""
    stores = deps.resolve(StoreBundle)

    # Prune message_index
    mi_cfg = MessageIndexConfig(**raw_config.get("message_index", {}))
    pruned = await stores.message_index.cleanup_older_than(mi_cfg.retention_days)
    if pruned:
        log.info(
            "message_index: pruned %d entries older than %d days",
            pruned,
            mi_cfg.retention_days,
        )

    await seed_grants_from_bots(stores.auth, stores.bot)

    circuit_registry, admin_user_ids, tg_bot_auths, dc_bot_auths = build_bot_auths(
        raw_config, stores.auth, stores.bot
    )

    bot_agent_map = await _resolve_bot_agent_map(
        stores.agent,
        [cfg for cfg, _ in tg_bot_auths],
        [cfg for cfg, _ in dc_bot_auths],
    )

    agent_configs = load_agent_configs(
        stores.agent, raw_config, set(bot_agent_map.values())
    )
    if not agent_configs:
        raise RuntimeError(
            "No agent configs could be loaded — run 'lyra agent init' to seed the"
            " agents table"
        )
    first_agent_config = agent_configs[next(iter(sorted(agent_configs)))]

    msg_manager = _load_messages(language=first_agent_config.i18n_language)

    pm = await build_pairing_manager(
        raw_config,
        vault_dir=vault_dir,
        auth_store=stores.auth,
        admin_user_ids=admin_user_ids,
    )

    stt_service = init_nats_stt(nc)
    await stt_service.start()
    tts_service = init_nats_tts(nc)
    await tts_service.start()
    nats_llm_client = await init_nats_llm(nc)

    inbound_bus, inbound_bus_cfg = build_inbound_bus(nc, raw_config)

    js = nc.jetstream()
    turn_publisher = TurnPublisher(js)
    adapter = TurnPublisherAdapter(turn_publisher, stores.turn)

    hub = build_hub(
        raw_config,
        circuit_registry=circuit_registry,
        msg_manager=msg_manager,
        pairing_manager=pm,
        stt_service=stt_service,
        tts_service=tts_service,
        prefs_store=stores.prefs,
        inbound_bus=inbound_bus,
        inbound_bus_cfg=inbound_bus_cfg,
        resume_publisher=adapter,
    )
    hub.set_turn_store(stores.turn)
    hub.set_message_index(stores.message_index)
    hub.cli_pool = None
    hub.set_turn_publisher(turn_publisher)

    typing_publisher = TypingPublisher(nc)
    hub.set_typing_publisher(typing_publisher)

    audit_sink = JetStreamAuditSink()
    await audit_sink.provision(nc)

    cli_nats_driver = await build_llm_client(nc)
    cli_nats_driver.set_turn_store(stores.turn)

    register_agents(
        hub,
        agent_configs,
        None,
        circuit_registry,
        msg_manager,
        stt_service,
        tts_service,
        stores.agent,
        raw_config,
        nats_llm_client,
        cli_nats_driver=cli_nats_driver,
    )

    tg_proxies, tg_dispatchers = wire_nats_telegram_proxies(
        NatsTgWiringDeps(
            hub=hub,
            nc=nc,
            tg_bot_auths=tg_bot_auths,
            bot_agent_map=bot_agent_map,
            circuit_registry=circuit_registry,
        )
    )
    dc_proxies, dc_dispatchers = wire_nats_discord_proxies(
        NatsDcWiringDeps(
            hub=hub,
            nc=nc,
            dc_bot_auths=dc_bot_auths,
            bot_agent_map=bot_agent_map,
            circuit_registry=circuit_registry,
        )
    )

    return HubSubsystem(
        hub=hub,
        inbound_bus=inbound_bus,
        inbound_bus_cfg=inbound_bus_cfg,
        tg_proxies=tg_proxies,
        dc_proxies=dc_proxies,
        tg_dispatchers=tg_dispatchers,
        dc_dispatchers=dc_dispatchers,
        stt_service=stt_service,
        tts_service=tts_service,
        nats_llm_client=nats_llm_client,
        cli_nats_driver=cli_nats_driver,
        audit_sink=audit_sink,
        typing_publisher=typing_publisher,
        turn_publisher=turn_publisher,
        pairing_manager=pm,
        circuit_registry=circuit_registry,
        admin_user_ids=admin_user_ids,
        tg_bot_auths=tg_bot_auths,
        dc_bot_auths=dc_bot_auths,
        bot_agent_map=bot_agent_map,
        agent_configs=agent_configs,
        msg_manager=msg_manager,
    )


async def compose_adapter(  # noqa: PLR0915, C901 — DEBT:migration-sequence-bootstrap
    raw_config: dict,
    nc: Any,
    platform: str,
    vault_dir: Path,
    deps: DependencyGraph,
) -> AdapterSubsystem:
    """Construct the adapter subsystem (no runtime lifecycle)."""
    platform_enum = Platform(platform)
    config_bundle = build_adapter_config_bundle(raw_config)

    entries: list[AdapterEntry] = []
    turn_store: Any = None
    thread_store: Any = None

    if platform == "telegram":
        from lyra.adapters.telegram import TelegramAdapter
        from lyra.config import TelegramMultiConfig
        from lyra.typing import TypingListener, make_typing_factory

        tg_multi_cfg = TelegramMultiConfig.model_validate(
            raw_config.get("telegram", {})
        )
        if not tg_multi_cfg.bots:
            raise RuntimeError("No telegram bots configured")

        tg_creds: dict[str, tuple[str, str | None]] = {}
        for bot_cfg in tg_multi_cfg.bots:
            bot_id = bot_cfg.bot_id
            tg_creds[bot_id] = credentials.load_bot_token("telegram", bot_id)

        turn_store = TurnStore(db_path=vault_dir / "turns.db")
        await turn_store.connect()

        for bot_cfg in tg_multi_cfg.bots:
            bot_id = bot_cfg.bot_id
            if bot_id not in tg_creds:
                continue
            token, webhook_secret = tg_creds[bot_id]

            inbound_bus = NatsBus(
                nc=nc,
                bot_id=bot_id,
                item_type=InboundMessage,
                publish_only=True,
            )
            inbound_bus.register(platform_enum)
            await inbound_bus.start()

            adapter = TelegramAdapter(
                bot_id=bot_id,
                token=token,
                inbound_bus=inbound_bus,
                webhook_secret=webhook_secret or "",
                turn_store=turn_store,
                tool_display_config=config_bundle.tool_display,
            )
            await adapter.resolve_identity()

            listener = NatsOutboundListener(
                nc,
                platform_enum,
                bot_id,
                adapter,
                queue_group=adapter_outbound(platform_enum.value, bot_id),
            )
            adapter._outbound_listener = listener

            from lyra.adapters.telegram.telegram import _telegram_scope_resolver
            from lyra.adapters.telegram.telegram_outbound import _typing_worker

            tg_typing_listener = TypingListener(
                nc=nc,
                subject=f"lyra.typing.telegram.{bot_id}",
                resolver=_telegram_scope_resolver,
                factory_builder=make_typing_factory(
                    partial(_typing_worker, adapter.bot)
                ),
                manager=adapter._typing,
            )
            await tg_typing_listener.start()

            try:
                await adapter.astart()
            except Exception:
                await close_safely(
                    "tg-adapter-start",
                    adapter.close(),
                    inbound_bus.stop(),
                    tg_typing_listener.stop(),
                )
                await close_safely(
                    "tg-wired",
                    *[
                        coro
                        for e in entries
                        for coro in (
                            e.adapter.close(),
                            e.inbound_bus.stop(),
                            e.typing_listener.stop(),
                        )
                    ],
                )
                if turn_store is not None:
                    await turn_store.close()
                raise

            entries.append(
                AdapterEntry(
                    adapter=adapter,
                    token=None,
                    inbound_bus=inbound_bus,
                    typing_listener=tg_typing_listener,
                    bot_id=bot_id,
                )
            )

    elif platform == "discord":
        from lyra.adapters.discord import DiscordAdapter
        from lyra.config import DiscordMultiConfig
        from lyra.typing import TypingListener, make_typing_factory

        dc_multi_cfg = DiscordMultiConfig.model_validate(
            raw_config.get("discord", {})
        )
        if not dc_multi_cfg.bots:
            raise RuntimeError("No discord bots configured")

        dc_creds: dict[str, str] = {}
        for bot_cfg in dc_multi_cfg.bots:
            bot_id = bot_cfg.bot_id
            token, _ = credentials.load_bot_token("discord", bot_id)
            dc_creds[bot_id] = token

        agent_store = AgentStore(db_path=vault_dir / "config.db")
        await agent_store.connect()
        dc_bot_watch_channels: dict[str, frozenset[int]] = {}
        try:
            for bot_cfg in dc_multi_cfg.bots:
                bot_settings = agent_store.get_bot_settings(
                    "discord", bot_cfg.bot_id
                )
                raw_ids = bot_settings.get("watch_channels", [])
                valid: list[int] = []
                for ch in raw_ids:
                    try:
                        valid.append(int(ch))
                    except (ValueError, TypeError):
                        log.warning(
                            "watch_channels: invalid channel id %r for bot %r"
                            " — skipping",
                            ch,
                            bot_cfg.bot_id,
                        )
                dc_bot_watch_channels[bot_cfg.bot_id] = frozenset(valid)
        finally:
            await agent_store.close()

        thread_store = ThreadStore(db_path=vault_dir / "discord.db")
        await thread_store.connect()

        turn_store = TurnStore(db_path=vault_dir / "turns.db")
        await turn_store.connect()

        for bot_cfg in dc_multi_cfg.bots:
            bot_id = bot_cfg.bot_id
            if bot_id not in dc_creds:
                continue
            token = dc_creds[bot_id]

            inbound_bus = NatsBus(
                nc=nc,
                bot_id=bot_id,
                item_type=InboundMessage,
                publish_only=True,
            )
            inbound_bus.register(platform_enum)
            await inbound_bus.start()

            adapter = DiscordAdapter(
                bot_id=bot_id,
                inbound_bus=inbound_bus,
                auto_thread=bot_cfg.auto_thread,
                thread_hot_hours=bot_cfg.thread_hot_hours,
                thread_store=thread_store,
                watch_channels=dc_bot_watch_channels.get(bot_id, frozenset()),
                turn_store=turn_store,
                tool_display_config=config_bundle.tool_display,
            )

            listener = NatsOutboundListener(
                nc,
                platform_enum,
                bot_id,
                adapter,
                queue_group=adapter_outbound(platform_enum.value, bot_id),
            )
            adapter._outbound_listener = listener
            try:
                await adapter.astart()
            except Exception:
                await close_safely(
                    "dc-adapter-start",
                    adapter.close(),
                    inbound_bus.stop(),
                )
                await close_safely(
                    "dc-wired",
                    *[
                        coro
                        for e in entries
                        for coro in (
                            e.adapter.close(),
                            e.inbound_bus.stop(),
                            e.typing_listener.stop(),
                        )
                    ],
                )
                if thread_store is not None:
                    await thread_store.close()
                if turn_store is not None:
                    await turn_store.close()
                raise

            from lyra.adapters.discord.adapter import _discord_scope_resolver
            from lyra.adapters.discord.discord_outbound import (
                _discord_typing_worker,
            )

            dc_typing_listener = TypingListener(
                nc=nc,
                subject=f"lyra.typing.discord.{bot_id}",
                resolver=_discord_scope_resolver,
                factory_builder=make_typing_factory(
                    partial(_discord_typing_worker, adapter._resolve_channel)
                ),
                manager=adapter._typing,
            )
            await dc_typing_listener.start()

            entries.append(
                AdapterEntry(
                    adapter=adapter,
                    token=token,
                    inbound_bus=inbound_bus,
                    typing_listener=dc_typing_listener,
                    bot_id=bot_id,
                )
            )

    else:
        raise RuntimeError(f"Unknown platform: {platform!r}")

    return AdapterSubsystem(
        entries=entries,
        turn_store=turn_store,
        thread_store=thread_store,
        config_bundle=config_bundle,
    )


async def compose_clipool(
    raw_config: dict,
    nc: Any,
    deps: DependencyGraph,
) -> ClipoolSubsystem:
    """Construct the CliPool worker subsystem (no runtime lifecycle)."""
    cli_pool_cfg = _load_cli_pool_config(raw_config)

    cli_pool = CliPool(
        idle_ttl=cli_pool_cfg.idle_ttl,
        default_timeout=cli_pool_cfg.default_timeout,
        reaper_interval=cli_pool_cfg.reaper_interval,
        kill_timeout=cli_pool_cfg.kill_timeout,
        read_buffer_bytes=cli_pool_cfg.read_buffer_bytes,
        stdin_drain_timeout=cli_pool_cfg.stdin_drain_timeout,
        max_idle_retries=cli_pool_cfg.max_idle_retries,
        intermediate_timeout=cli_pool_cfg.intermediate_timeout,
    )
    await cli_pool.start()

    worker = CliPoolNatsWorker(
        cli_pool,
        timeout=cli_pool_cfg.default_timeout,
        identity_name="clipool-worker",
    )

    return ClipoolSubsystem(
        cli_pool=cli_pool,
        worker=worker,
        cli_pool_cfg=cli_pool_cfg,
    )
