"""Build LLM client for hub — connects to clipool worker via NATS (phase 2)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from nats.aio.client import Client as NATS

if TYPE_CHECKING:
    from factory.llm.drivers.claude_rpc import ClaudeRpcDriver

log = logging.getLogger(__name__)


async def build_llm_client(
    nc: NATS,
    *,
    timeout: float = 120.0,
) -> "ClaudeRpcDriver":
    """Build ClaudeRpcDriver — JobEnvelope dispatch + per-job pub/sub."""
    from factory.llm.drivers.claude_rpc import ClaudeRpcDriver

    return ClaudeRpcDriver(nc, timeout_s=timeout)