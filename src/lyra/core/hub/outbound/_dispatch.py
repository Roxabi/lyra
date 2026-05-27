"""Dispatch helper — extracted from outbound_dispatcher.py for line count management."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ...circuit_breaker import CircuitBreaker
from ...messaging.callbacks import unwrap_callback
from ...messaging.message import RoutingContext
from .outbound_errors import (
    _CIRCUIT_NOTIFY_DEBOUNCE,
    _CIRCUIT_OPEN_MSG,
    _ITEM,
    _SEND_ERROR_MSG,
    _is_transient_error,
)

if TYPE_CHECKING:
    from lyra.core.hub import ChannelAdapter

log = logging.getLogger(__name__)

_BACKOFF_DELAYS = (1.0, 2.0, 4.0)
_MAX_ATTEMPTS = 4


@dataclass
class RoutedPayload:
    kind: str
    msg: Any
    payload: Any
    outbound: Any | None
    routing: RoutingContext
    callback_target: Any | None


async def _resolve_item(item: _ITEM, verify_routing_fn) -> RoutedPayload | None:
    kind = item[0]
    if kind == "streaming":
        _, msg, payload, outbound = item
    elif kind in (
        "send",
        "audio",
        "audio_stream",
        "voice_stream",
        "attachment",
    ):
        _, msg, payload = item
        outbound = None
    else:
        log.error(
            '{"event": "unknown_kind", "kind": "%s", "action": "skipped"}',
            kind,
        )
        return None

    if kind == "send":
        _routing = getattr(payload, "routing", None) or msg.routing
    elif kind == "voice_stream":
        _routing = msg.routing or RoutingContext(
            platform=msg.platform,
            bot_id=msg.bot_id,
            scope_id=msg.scope_id,
        )
    elif kind in ("audio", "audio_stream", "attachment"):
        _routing = msg.routing
    else:
        _routing = outbound.routing if outbound is not None else msg.routing

    if not verify_routing_fn(_routing):
        if kind in ("streaming", "audio_stream", "voice_stream"):
            async for _ in payload:
                pass
        return None

    callback_target = payload if kind == "send" else outbound
    return RoutedPayload(
        kind=kind,
        msg=msg,
        payload=payload,
        outbound=outbound,
        routing=_routing,
        callback_target=callback_target,
    )


async def _try_send(
    adapter: "ChannelAdapter",
    kind: str,
    msg: Any,
    payload: Any,
    outbound: Any | None,
) -> bool:
    """Attempt one send/render. Returns True if sent, False if superseded (drained)."""
    if kind == "send":
        await adapter.send(msg, payload)
    elif kind == "audio":
        await adapter.render_audio(payload, msg)
    elif kind == "audio_stream":
        await adapter.render_audio_stream(payload, msg)
    elif kind == "voice_stream":
        await adapter.render_voice_stream(payload, msg)
    elif kind == "attachment":
        await adapter.render_attachment(payload, msg)
    else:
        if outbound is not None and outbound.metadata.get("_superseded"):
            async for _ in payload:
                pass
            return False
        await adapter.send_streaming(msg, payload, outbound)
    return True


async def _send_with_retry(  # noqa: PLR0913
    platform_name: str,
    adapter: "ChannelAdapter",
    circuit: CircuitBreaker | None,
    kind: str,
    msg: Any,
    payload: Any,
    outbound: Any | None = None,
) -> Exception | None:
    _last_exc: Exception | None = None
    _attempt = 0
    while _attempt < _MAX_ATTEMPTS:
        try:
            sent = await _try_send(adapter, kind, msg, payload, outbound)
            if sent and circuit is not None:
                circuit.record_success()
            _last_exc = None
            break  # success
        except BaseException as exc:
            if not isinstance(exc, Exception):
                # Re-raise CancelledError / KeyboardInterrupt immediately
                if circuit is not None:
                    circuit.record_failure()
                raise
            is_transient = _is_transient_error(exc)
            retry_possible = (
                is_transient
                and _attempt + 1 < _MAX_ATTEMPTS
                and kind == "send"  # streaming iterators cannot be replayed
            )
            if retry_possible:
                delay = _BACKOFF_DELAYS[_attempt]
                log.warning(
                    "OutboundDispatcher[%s] delivery attempt %d failed"
                    " (kind=%s, transient), retrying in %.0fs: %s",
                    platform_name,
                    _attempt + 1,
                    kind,
                    delay,
                    exc,
                )
                _last_exc = exc
                _attempt += 1
                await asyncio.sleep(delay)
            else:
                _last_exc = exc
                _attempt = _MAX_ATTEMPTS  # exit loop
                break
    return _last_exc


async def _handle_post_send(  # noqa: PLR0913
    kind: str,
    payload: Any,
    outbound: Any | None,
    msg: Any,
    platform_name: str,
    circuit: CircuitBreaker | None,
    try_notify_fn: Any,
    _last_exc: Exception | None,
) -> None:
    # Invoke dispatched callback after send (#316).
    # "send" → payload is the OutboundMessage; else → outbound.
    _out = payload if kind == "send" else outbound
    if _out is not None:
        _dispatched = unwrap_callback(_out.metadata, "_on_dispatched", pop=True)
        if _dispatched is not None:
            await _dispatched(_out)

    if _last_exc is not None:
        exc = _last_exc
        if circuit is not None:
            circuit.record_failure()
        # Drain iterator to prevent generator leaks on delivery failure
        if kind in ("streaming", "audio_stream", "voice_stream"):
            async for _ in payload:
                pass
        log.error(
            "OutboundDispatcher[%s] delivery failed (kind=%s): %s",
            platform_name,
            kind,
            exc,
            exc_info=exc,
        )
        # Fix 1: send user notification after all retries exhausted
        # Gate on circuit: skip if circuit already open to avoid duplicate
        # notifications (the circuit-open debounce already informed the user).
        if kind in ("send", "streaming"):
            await try_notify_fn(
                msg,
                _SEND_ERROR_MSG,
            )


async def dispatch_outbound_item(  # noqa: PLR0913
    platform_name: str,
    adapter: "ChannelAdapter",
    circuit: CircuitBreaker | None,
    item: _ITEM,
    verify_routing_fn: Any,
    try_notify_fn: Any,
    circuit_notify_ts: dict[str, float],
) -> None:
    """Dispatch a single item (routing check, circuit, retry, send, callback)."""
    routed = await _resolve_item(item, verify_routing_fn)
    if routed is None:
        return

    if circuit is not None and circuit.is_open():
        log.warning(
            '{"event": "%s_circuit_open", "action": "%s", "dropped": true}',
            platform_name,
            routed.kind,
        )
        # Drain streaming iterator to prevent generator leaks
        if routed.kind in ("streaming", "audio_stream", "voice_stream"):
            async for _ in routed.payload:
                pass
        _cb_out = routed.callback_target
        if _cb_out is not None:
            _cb_out.metadata["reply_message_id"] = None
            _cb = unwrap_callback(_cb_out.metadata, "_on_dispatched", pop=True)
            if _cb is not None:
                await _cb(_cb_out)
        # Fix 3: notify user once per chat per debounce window
        scope_key = routed.msg.scope_id or routed.msg.id
        now = time.monotonic()
        last_ts = circuit_notify_ts.get(scope_key, 0.0)
        if now - last_ts >= _CIRCUIT_NOTIFY_DEBOUNCE:
            circuit_notify_ts[scope_key] = now
            await try_notify_fn(routed.msg, _CIRCUIT_OPEN_MSG)
        return

    exc = await _send_with_retry(
        platform_name=platform_name,
        adapter=adapter,
        circuit=circuit,
        kind=routed.kind,
        msg=routed.msg,
        payload=routed.payload,
        outbound=routed.outbound,
    )

    await _handle_post_send(
        kind=routed.kind,
        payload=routed.payload,
        outbound=routed.outbound,
        msg=routed.msg,
        platform_name=platform_name,
        circuit=circuit,
        try_notify_fn=try_notify_fn,
        _last_exc=exc,
    )
