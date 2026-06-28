"""Shared dataclasses used across bootstrap subpackages.

Neutral home for types that would otherwise create cross-subpackage cycles
between factory/ and lifecycle/.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from pathlib import Path

    from nats.aio.client import Client as NatsClient
    from nats.js import JetStreamContext

    from factory.adapters.discord import DiscordAdapter
    from factory.adapters.telegram import TelegramAdapter
    from factory.bootstrap.bootstrap_stores import StoreBundle
    from factory.config import DiscordBotConfig, TelegramBotConfig
    from factory.core.agent import Agent
    from factory.core.auth.authenticator import Authenticator
    from factory.core.cli.cli_pool import CliPool
    from factory.core.hub import Hub, OutboundDispatcher
    from factory.core.lifecycle.circuit_breaker import CircuitRegistry
    from factory.core.messaging.messages import MessageManager
    from factory.core.ports.blobstore import BlobStorePort
    from factory.core.ports.llm import LlmProvider
    from factory.core.ports.stt import STTProtocol
    from factory.core.ports.tts import TtsProtocol
    from factory.infrastructure.audit import JetStreamAuditSink
    from factory.infrastructure.stores.identity.pairing import PairingManager
    from factory.infrastructure.stores.session.thread_store import ThreadStore
    from factory.llm.llm_client import LlmClient
    from factory.nats.nats_bus import NatsBus
    from factory.nats.nats_channel_proxy import NatsChannelProxy
    from factory.nats.socialmedia.nats_socialmedia_client import NatsSocialMediaClient
    from factory.typing import TypingListener


class DiscordAdapterEntry(NamedTuple):
    adapter: DiscordAdapter
    config: DiscordBotConfig
    token: str


@dataclass
class WiredAdapters:
    tg_adapters: list[TelegramAdapter]
    tg_dispatchers: list[OutboundDispatcher]
    dc_adapters: list[DiscordAdapterEntry]
    dc_dispatchers: list[OutboundDispatcher]
    dc_thread_store: ThreadStore | None
    tg_typing_listeners: list[TypingListener] = field(default_factory=list)
    dc_typing_listeners: list[TypingListener] = field(default_factory=list)
    tg_consumers: list = field(default_factory=list)
    dc_consumers: list = field(default_factory=list)


@dataclass
class LifecycleResources:
    """Optional infrastructure wired into the lifecycle."""

    pm: PairingManager | None
    # unified: always None; CliPool managed in unified.py finally
    cli_pool: CliPool | None
    proxies: list[NatsChannelProxy] = field(default_factory=list)
    nc: NatsClient | None = field(default=None)


# ---------------------------------------------------------------------------
# Return bundles (plain dataclasses — no framework imports)
# ---------------------------------------------------------------------------


@dataclass
class VoiceBundle:
    stt_service: STTProtocol | None
    tts_service: TtsProtocol | None
    nats_llm_client: "LlmClient | None"
    socialmedia_client: "NatsSocialMediaClient | None" = None


@dataclass
class BotAuthBundle:
    tg_bot_auths: list[tuple[TelegramBotConfig, Authenticator]]
    dc_bot_auths: list[tuple[DiscordBotConfig, Authenticator]]
    bot_agent_map: dict
    agent_configs: dict[str, Agent]
    first_agent_config: Agent
    msg_manager: MessageManager
    circuit_registry: CircuitRegistry
    admin_user_ids: frozenset[str]


@dataclass
class CliPoolBundle:
    cli_pool: CliPool
    cli_nats_driver: "LlmProvider | None"
    worker: object
    audit_sink: JetStreamAuditSink


# ---------------------------------------------------------------------------
# DI containers
# ---------------------------------------------------------------------------


@dataclass
class BuildHubDeps:
    raw_config: dict
    bundle: BotAuthBundle
    voice: VoiceBundle
    inbound_bus: NatsBus
    pm: PairingManager | None
    stores: StoreBundle
    blob_store: "BlobStorePort | None" = None


@dataclass
class RegisterAgentsDeps:
    hub: Hub
    bundle: BotAuthBundle
    voice: VoiceBundle
    clipool: CliPoolBundle
    raw_config: dict
    stores: StoreBundle
    omp_rpc_driver: "LlmProvider | None" = None


@dataclass
class WireAdaptersDeps:
    hub: Hub
    bundle: BotAuthBundle
    nc: NatsClient
    stores: StoreBundle
    vault_dir: Path
    raw_config: dict
    blob_store: "BlobStorePort | None" = None
    js: "JetStreamContext | None" = None
