"""NatsLlmDriver — LlmProvider over NATS request-reply (complete + stream)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from lyra.core.messaging.events import (
    LlmEvent,
    ResultLlmEvent,
    TextLlmEvent,
    ToolUseLlmEvent,
)
from lyra.llm.base import LlmResult

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS
    from nats.aio.subscription import Subscription

    from lyra.core.agent.agent_config import ModelConfig

log = logging.getLogger(__name__)

# Module-level aliases kept for backward compatibility (tests may import these).
SUBJECT_REQUEST = "lyra.llm.request"
HB_SUBJECT_PATTERN = "lyra.llm.health.*"
HB_TTL = 30.0  # worker considered alive if last heartbeat is within this window


class NatsLlmDriver:
    """LlmProvider dispatching inference to a NATS worker."""

    SUBJECT_REQUEST: str = "lyra.llm.request"
    HB_SUBJECT_PATTERN: str = "lyra.llm.health.*"
    #: Worker considered alive if last heartbeat is within this window.
    HB_TTL: float = 30.0

    capabilities: dict[str, Any] = {"streaming": True, "auth": "nats"}

    def __init__(self, nc: "NATS", *, timeout: float = 120.0) -> None:
        self._nc = nc
        self._timeout = timeout
        self._worker_freshness: dict[str, float] = {}
        self._hb_sub: "Subscription | None" = None

    async def start(self) -> None:
        """Subscribe to heartbeat subject. Idempotent."""
        if self._hb_sub is None:
            self._hb_sub = await self._nc.subscribe(
                self.HB_SUBJECT_PATTERN, cb=self._on_heartbeat
            )
            log.debug("NatsLlmDriver: subscribed to %s", self.HB_SUBJECT_PATTERN)

    async def stop(self) -> None:
        """Unsubscribe from heartbeat subject. Idempotent."""
        if self._hb_sub is not None:
            try:
                await self._hb_sub.unsubscribe()
            except Exception:
                log.debug("NatsLlmDriver: error unsubscribing heartbeat", exc_info=True)
            finally:
                self._hb_sub = None

    async def _on_heartbeat(self, msg: Any) -> None:
        try:
            data = json.loads(msg.data)
            worker_id = data.get("worker_id")
            if not worker_id:
                log.warning("nats_llm: heartbeat missing worker_id, ignoring")
                return
            self._worker_freshness[worker_id] = time.monotonic()
            log.debug("nats_llm: heartbeat from worker_id=%s", worker_id)
        except Exception:
            log.debug("nats_llm: heartbeat parse error", exc_info=True)

    def _any_worker_alive(self) -> bool:
        """Prune stale entries, return True if any worker is within HB_TTL."""
        now = time.monotonic()
        # Evict workers whose last heartbeat was more than TTL*2 ago.
        self._worker_freshness = {
            k: v
            for k, v in self._worker_freshness.items()
            if now - v <= self.HB_TTL * 2
        }
        return any(now - ts <= self.HB_TTL for ts in self._worker_freshness.values())

    def is_alive(self, pool_id: str) -> bool:
        """Return True when NATS is connected and at least one worker is fresh."""
        del pool_id  # LlmProvider protocol slot; driver is stateless per-pool
        return self._nc.is_connected and self._any_worker_alive()

    async def complete(  # noqa: PLR0913
        self,
        pool_id: str,
        text: str,
        model_cfg: "ModelConfig",
        system_prompt: str,
        *,
        messages: list[dict] | None = None,
    ) -> LlmResult:
        """Dispatch a single-turn completion over NATS request-reply."""
        request_id = str(uuid4())
        payload_dict: dict[str, Any] = {
            "request_id": request_id,
            "pool_id": pool_id,
            "text": text,
            "model_cfg": model_cfg.model_dump(),
            "system_prompt": system_prompt,
            "messages": messages or [],
            "stream": False,
        }
        payload = json.dumps(payload_dict, ensure_ascii=False).encode("utf-8")

        try:
            reply = await self._nc.request(
                self.SUBJECT_REQUEST, payload, timeout=self._timeout
            )
        except TimeoutError:
            log.warning(
                "nats_llm: complete() timeout after %.0fs [pool:%s]",
                self._timeout,
                pool_id,
            )
            return LlmResult(
                error=f"LLM worker timeout after {self._timeout:.0f}s",
                retryable=True,
            )
        except Exception as exc:
            log.warning(
                "nats_llm: complete() transport error [pool:%s]: %s: %s",
                pool_id,
                type(exc).__name__,
                exc,
            )
            return LlmResult(
                error=f"NATS transport error: {exc}",
                retryable=True,
            )

        try:
            data: dict = json.loads(reply.data)
        except json.JSONDecodeError:
            log.warning(
                "nats_llm: complete() invalid JSON from worker [pool:%s]",
                pool_id,
            )
            return LlmResult(error="Invalid JSON from worker", retryable=True)

        error = data.get("error", "")
        retryable = bool(data.get("retryable", True))
        if error:
            log.warning(
                "nats_llm: worker error [pool:%s] retryable=%s: %s",
                pool_id,
                retryable,
                error,
            )
            return LlmResult(error=error, retryable=retryable)

        return LlmResult(
            result=data.get("result", ""),
            session_id=data.get("session_id", ""),
            error="",
            retryable=True,
        )

    async def stream(  # noqa: PLR0913
        self,
        pool_id: str,
        text: str,
        model_cfg: "ModelConfig",
        system_prompt: str,
        *,
        messages: list[dict] | None = None,
    ) -> AsyncIterator[LlmEvent]:
        """Return an async generator of LlmEvents for a streaming request.

        Creates an ephemeral inbox, publishes the request with the inbox as
        reply subject, then iterates messages arriving on the inbox and parses
        them into ``TextLlmEvent`` / ``ToolUseLlmEvent`` / ``ResultLlmEvent``.
        The loop exits when a chunk with ``done=true`` is received.

        The inbox subscription is always unsubscribed in the ``finally`` block
        — this covers both normal termination and caller cancellation.
        """
        return self._stream_gen(
            pool_id, text, model_cfg, system_prompt, messages=messages
        )

    async def _stream_gen(  # noqa: C901, PLR0913
        self,
        pool_id: str,
        text: str,
        model_cfg: "ModelConfig",
        system_prompt: str,
        *,
        messages: list[dict] | None = None,
    ) -> AsyncIterator[LlmEvent]:
        """Async generator: yield LlmEvents from an ephemeral inbox subscription."""
        request_id = str(uuid4())
        inbox = self._nc.new_inbox()

        # Queue fed by the subscription callback; bounded to avoid unbounded growth.
        queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=512)

        async def _on_msg(msg: Any) -> None:
            await queue.put(msg)

        sub = await self._nc.subscribe(inbox, cb=_on_msg)

        payload_dict: dict[str, Any] = {
            "request_id": request_id,
            "pool_id": pool_id,
            "text": text,
            "model_cfg": model_cfg.model_dump(),
            "system_prompt": system_prompt,
            "messages": messages or [],
            "stream": True,
        }
        payload = json.dumps(payload_dict, ensure_ascii=False).encode("utf-8")

        try:
            await self._nc.publish(self.SUBJECT_REQUEST, payload, reply=inbox)

            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=self._timeout)
                except TimeoutError:
                    log.warning("nats_llm: stream() inbox timeout [pool:%s]", pool_id)
                    yield ResultLlmEvent(
                        is_error=True,
                        duration_ms=0,
                    )
                    return

                try:
                    chunk: dict = json.loads(msg.data)
                except Exception:
                    log.debug(
                        "nats_llm: stream chunk parse error [pool:%s]",
                        pool_id,
                        exc_info=True,
                    )
                    continue

                event_type = chunk.get("event_type", "text")
                done = bool(chunk.get("done", False))

                if event_type == "text":
                    text_chunk = chunk.get("text", "")
                    if text_chunk:
                        yield TextLlmEvent(text=text_chunk)
                elif event_type == "tool_use":
                    yield ToolUseLlmEvent(
                        tool_name=chunk.get("tool_name", ""),
                        tool_id=chunk.get("tool_id", ""),
                        input=chunk.get("input", {}),
                    )
                elif event_type == "result":
                    yield ResultLlmEvent(
                        is_error=bool(chunk.get("is_error", False)),
                        duration_ms=int(chunk.get("duration_ms", 0)),
                    )
                    return

                if done:
                    # Emit a result sentinel if the worker sets done=true on a
                    # non-result chunk (defensive — spec requires a result chunk).
                    if event_type != "result":
                        log.warning(
                            "nats_llm: worker sent done=True on event_type=%r"
                            " without result chunk [pool:%s]",
                            event_type,
                            pool_id,
                        )
                        yield ResultLlmEvent(is_error=False, duration_ms=0)
                    return

        finally:
            try:
                await sub.unsubscribe()
            except Exception:
                log.debug(
                    "nats_llm: error unsubscribing inbox [pool:%s]",
                    pool_id,
                    exc_info=True,
                )
