"""Web smoke standalone adapter wiring."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from functools import partial
from typing import Any

from factory.adapters.web import WEB_BOT_ID, WebAdapter
from factory.bootstrap.factory.config import AdapterConfigBundle
from factory.bootstrap.lifecycle.lifecycle_helpers import close_safely
from factory.bootstrap.lifecycle.signal_handlers import setup_shutdown_event
from factory.bootstrap.wiring._standalone_wiring_common import (
    TypingDeps,
    wire_bot_common,
)
from factory.bootstrap.wiring.kv_agent_roster import seed_web_agent_roster
from factory.core.messaging.message import Platform
from roxabi_nats.readiness import wait_for_hub

log = logging.getLogger(__name__)


async def _noop_typing_worker(*_args: Any, **_kwargs: Any) -> None:
    """Web smoke adapter has no platform typing indicator."""


async def _close_web_wired(wired: list[tuple]) -> None:
    close_coros = [
        coro
        for adapter, ibus, tl, consumer in wired
        for coro in (adapter.close(), ibus.stop(), tl.stop(), consumer.stop())
    ]
    await close_safely("web", *close_coros)


async def bootstrap_web_standalone(
    nc: Any,
    raw_config: dict,
    config_bundle: AdapterConfigBundle,
    platform_enum: Platform,
    *,
    _stop: asyncio.Event | None = None,
) -> None:
    """Bootstrap the web smoke adapter connected to NATS."""
    del raw_config
    await wait_for_hub(nc)
    js = nc.jetstream()
    agent_names = await seed_web_agent_roster(js)
    if not agent_names:
        sys.exit("No agents in roster.web — hub must publish agent roster first")

    host = os.environ.get("FACTORY_WEB_HOST", "0.0.0.0")
    port = int(os.environ.get("FACTORY_WEB_PORT", "8765"))
    bot_id = WEB_BOT_ID

    wired: list[tuple] = []

    def _web_factory(inbound_bus: Any) -> tuple:
        adapter = WebAdapter(
            bot_id=bot_id,
            inbound_bus=inbound_bus,
            host=host,
            port=port,
            agent_names=agent_names,
        )
        adapter.set_nats_client(nc)
        typing_deps = TypingDeps(
            subject=f"factory.typing.web.{bot_id}",
            scope_resolver=lambda _scope: 0,
            worker_factory=partial(_noop_typing_worker),
        )
        return adapter, typing_deps

    wired.append(
        await wire_bot_common(
            nc=nc,
            platform_enum=platform_enum,
            bot_id=bot_id,
            adapter_factory=_web_factory,
            config_bundle=config_bundle,
            js=js,
            blob_store=None,
        )
    )

    stop = setup_shutdown_event(_stop)

    try:
        await stop.wait()
    finally:
        await _close_web_wired(wired)
