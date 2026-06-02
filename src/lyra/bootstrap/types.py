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

    from lyra.adapters.discord import DiscordAdapter
    from lyra.adapters.telegram import TelegramAdapter
    from lyra.bootstrap.bootstrap_stores import StoreBundle
    from lyra.config import DiscordBotConfig, TelegramBotConfig
    from lyra.core.agent import Agent
    from lyra.core.auth.authenticator import Authenticator
    from lyra.core.cli.cli_pool import CliPool
    from lyra.core.hub import Hub, OutboundDispatcher
    from lyra.core.lifecycle.circuit_breaker import CircuitRegistry
    from lyra.core.messaging.messages import MessageManager
    from lyra.core.ports.blobstore import BlobStorePort
    from lyra.core.ports.stt import STTProtocol
    from lyra.core.ports.tts import TtsProtocol
    from lyra.infrastructure.audit import JetStreamAuditSink
    from lyra.infrastructure.stores.pairing import PairingManager
    from lyra.infrastructure.stores.thread_store import ThreadStore
    from lyra.llm.llm_client import LlmClient
    from lyra.nats.nats_bus import NatsBus
    from lyra.nats.nats_channel_proxy import NatsChannelProxy
    from lyra.typing import TypingListener


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
    cli_nats_driver: "LlmClient | None"
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


@dataclass
class WireAdaptersDeps:
    hub: Hub
    bundle: BotAuthBundle
    nc: NatsClient
    stores: StoreBundle
    vault_dir: Path
    raw_config: dict
    blob_store: "BlobStorePort | None" = None
