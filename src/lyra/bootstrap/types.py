"""Shared dataclasses used across bootstrap subpackages.

Neutral home for types that would otherwise create cross-subpackage cycles
between factory/ and lifecycle/.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nats.aio.client import Client as NatsClient

    from lyra.adapters.discord import DiscordAdapter
    from lyra.adapters.telegram import TelegramAdapter
    from lyra.config import DiscordBotConfig
    from lyra.core.hub import OutboundDispatcher
    from lyra.infrastructure.stores.thread_store import ThreadStore

from lyra.core.cli.cli_pool import CliPool
from lyra.infrastructure.stores.pairing import PairingManager
from lyra.nats.nats_channel_proxy import NatsChannelProxy


@dataclass
class WiredAdapters:
    tg_adapters: list[TelegramAdapter]
    tg_dispatchers: list[OutboundDispatcher]
    dc_adapters: list[tuple[DiscordAdapter, DiscordBotConfig, str]]
    dc_dispatchers: list[OutboundDispatcher]
    dc_thread_store: ThreadStore | None


@dataclass
class LifecycleResources:
    """Optional infrastructure wired into the lifecycle."""

    pm: PairingManager | None
    # unified: always None; CliPool managed in unified.py finally
    cli_pool: CliPool | None
    proxies: list[NatsChannelProxy] | None = field(default=None)
    nc: NatsClient | None = field(default=None)
