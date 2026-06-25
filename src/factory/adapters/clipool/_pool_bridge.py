"""Pool operation bridge — error boundary between CliPool and NATS replies."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from factory.adapters.clipool._worker_helpers import _make_ack, _make_chunk
from factory.adapters.clipool.error_classifier import classify_exception
from factory.core.messaging.utils.metrics import emit_populated_total

log = logging.getLogger(__name__)

ReplyFn = Callable[[Any, bytes], Awaitable[None]]


async def publish_pool_error(
    *,
    msg: Any,
    pool_id: str,
    exc: BaseException,
    reply: ReplyFn,
    nc: Any | None,
    direct_publish: bool,
) -> None:
    """Classify *exc*, emit metrics, and reply with a terminal error chunk."""
    log.exception("clipool_worker: pool send failed for pool_id=%r", pool_id)
    worker_error = classify_exception(exc)
    emit_populated_total(domain="cli")
    chunk = _make_chunk(
        pool_id,
        event_type="error",
        is_error=True,
        done=True,
        worker_error=worker_error,
    )
    if direct_publish and msg.reply and nc:
        await nc.publish(msg.reply, chunk)
    else:
        await reply(msg, chunk)


async def run_pool_op(
    *,
    coro: Awaitable[Any],
    pool_id: str,
    msg: Any,
    reply: ReplyFn,
    nc: Any | None,
    direct_publish: bool,
    control_ack: bool = False,
) -> Any | bytes | None:
    """Await *coro*; on failure classify and reply (or return a control NAK)."""
    try:
        return await coro
    except Exception as exc:  # noqa: BLE001 — DEBT:boundary-broad-catch# boundary: adapter I/O — pool bridge; classify_exception sanitizes
        if control_ack:
            log.exception(
                "clipool_worker: control op failed for pool_id=%r",
                pool_id,
            )
            return _make_ack(pool_id, ok=False)
        await publish_pool_error(
            msg=msg,
            pool_id=pool_id,
            exc=exc,
            reply=reply,
            nc=nc,
            direct_publish=direct_publish,
        )
        return None