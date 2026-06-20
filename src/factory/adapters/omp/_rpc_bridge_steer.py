"""Steer subscription and in-flight guard for RpcBridge."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from roxabi_contracts.jobs.subjects import jobs_steer

if TYPE_CHECKING:
    from nats.aio.client import Client as NatsClient

log = logging.getLogger(__name__)


class SteerViolationError(Exception):
    """Raised when steer() is attempted while prompt_and_wait is in flight."""


class RpcBridgeSteerMixin:
    """Steer bridge: subscribe factory.job.<job_id>.steer during prompt_and_wait."""

    _client: Any
    _in_prompt_await: bool

    async def _subscribe_steer(self, nc: NatsClient, job_id: str) -> Any | None:
        async def _handle_steer_msg(msg: Any) -> None:
            if not self._in_prompt_await:
                log.debug("rpc_bridge: steer message dropped — no active job")
                return
            text = msg.data.decode("utf-8", errors="replace")
            await asyncio.to_thread(self._client.steer, text)

        return await nc.subscribe(jobs_steer(job_id), cb=_handle_steer_msg)

    async def steer(self, job_id: str, text: str) -> None:
        """Fire-and-forget steer; must NOT be called inside prompt_and_wait."""
        if self._in_prompt_await:
            raise SteerViolationError(
                "steer() called while prompt_and_wait is in flight"
            )
        await asyncio.to_thread(self._client.steer, text)
