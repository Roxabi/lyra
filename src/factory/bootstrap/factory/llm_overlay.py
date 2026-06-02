"""LLM overlay helpers — 3-layer DI composition for NATS LLM."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from roxabi_nats.connect import scrub_nats_url

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS

    from factory.llm.llm_client import LlmClient

log = logging.getLogger(__name__)


async def init_nats_llm(nc: "NATS | None") -> "LlmClient | None":
    """Compose 3-layer NATS LLM stack (transport → pool → client) if NATS_URL set."""
    if nc is None or not os.environ.get("NATS_URL"):
        return None

    from factory.llm.cli_nats_codec import CliNatsCodec
    from factory.llm.llm_client import LlmClient
    from factory.nats.worker_registry import WorkerRegistry
    from factory.transport.nats_request_response import NatsTransport
    from factory.transport.worker_pool_client import WorkerPoolClient
    from roxabi_contracts.llm import SUBJECTS, validate_worker_id

    transport = NatsTransport(nc)
    pool = WorkerPoolClient(
        transport,
        registry=WorkerRegistry(),
        hb_subject=SUBJECTS.heartbeat,
        validate_worker_id=validate_worker_id,
        name="llm",
    )
    await pool.start(nc)
    client = LlmClient(pool, CliNatsCodec(), request_subject=SUBJECTS.generate_request)
    log.info(
        "LlmClient: initialised 3-layer (NATS_URL=%s)",
        scrub_nats_url(os.environ.get("NATS_URL", "")),
    )
    return client
