"""Build LLM client for hub — connects to clipool worker via NATS."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from nats.aio.client import Client as NATS

if TYPE_CHECKING:
    from factory.llm.llm_client import LlmClient

log = logging.getLogger(__name__)


async def build_llm_client(
    nc: NATS,
    *,
    timeout: float = 120.0,
) -> "LlmClient":
    """Build and start an LlmClient connected to the clipool worker via NATS."""
    from factory.llm.cli_pool_codec import CliPoolCodec
    from factory.llm.llm_client import LlmClient
    from factory.nats.worker_registry import WorkerRegistry
    from factory.transport.nats_request_response import NatsTransport
    from factory.transport.worker_pool_client import WorkerPoolClient
    from roxabi_contracts._nats_utils import validate_worker_id

    transport = NatsTransport(nc)
    pool = WorkerPoolClient(
        transport,
        registry=WorkerRegistry(),
        hb_subject="lyra.clipool.heartbeat",
        validate_worker_id=validate_worker_id,
        name="clipool",
    )
    await pool.start(nc)
    return LlmClient(
        pool,
        CliPoolCodec(),
        timeout=timeout,
        request_subject="lyra.clipool.cmd",
    )
