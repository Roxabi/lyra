"""LLM overlay helpers — NATS LLM driver initialisation."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from roxabi_nats.connect import scrub_nats_url

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS

    from lyra.nats.nats_llm_client import NatsLlmClient

log = logging.getLogger(__name__)


async def init_nats_llm(nc: "NATS | None") -> "NatsLlmClient | None":
    """Create and start a NatsLlmClient if NATS_URL is set, else None.

    Returns a started client (heartbeat subscription active). Callers must
    call ``await client.stop()`` on shutdown. When ``nc`` is None or
    NATS_URL is unset, returns None and the ``"nats"`` backend is simply
    not registered — ``ProviderRegistry.get()`` raises KeyError for any
    agent configured with ``backend = "nats"``.
    """
    if nc is None or not os.environ.get("NATS_URL"):
        return None

    from lyra.nats.nats_llm_client import NatsLlmClient

    client = NatsLlmClient(nc=nc)
    await client.start()
    log.info(
        "NatsLlmClient: initialised (NATS_URL=%s)",
        scrub_nats_url(os.environ.get("NATS_URL", "")),
    )
    return client
