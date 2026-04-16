"""Dispatch helper — extracted from outbound_dispatcher.py for line count management."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from typing import TYPE_CHECKING

from ..circuit_breaker import CircuitBreaker
from ..message import RoutingContext
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


async def dispatch_outbound_item(  # noqa: C901, PLR0913, PLR0915
    platform_name: str,
    adapter: "ChannelAdapter",
    circuit: CircuitBreaker | None,
    item: _ITEM,
    verify_routing_fn,
    try_notify_fn,
    circuit_notify_ts: dict[str, float],
) -> None:
    """Dispatch a single item (routing check, circuit, retry, send, callback)."""
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
        return
    # Verify routing context matches this dispatcher.
    # "send": check payload (OutboundMessage) routing.
    # "streaming": check outbound routing if provided.
    # "audio"/"audio_stream"/"attachment": use msg (InboundMessage) routing.
    # "voice_stream": synthesize from msg when routing absent — TTS callers
    #     may omit explicit routing; synthesizing ensures the routing check
    #     validates platform+bot_id without requiring callers to set it.
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
        return

    if circuit is not None and circuit.is_open():
        log.warning(
            '{"event": "%s_circuit_open", "action": "%s", "dropped": true}',
            platform_name,
            kind,
        )
        # Drain streaming iterator to prevent generator leaks
        if kind in ("streaming", "audio_stream", "voice_stream"):
            async for _ in payload:
                pass
        _cb_out = payload if kind == "send" else outbound
        if _cb_out is not None:
            _cb_out.metadata["reply_message_id"] = None
            _cb = _cb_out.metadata.pop("_on_dispatched", None)
            if callable(_cb):
                _cb_result = _cb(_cb_out)
                if inspect.isawaitable(_cb_result):
                    await _cb_result
        # Fix 3: notify user once per chat per debounce window
        scope_key = msg.scope_id or msg.id
        now = time.monotonic()
        last_ts = circuit_notify_ts.get(scope_key, 0.0)
        if now - last_ts >= _CIRCUIT_NOTIFY_DEBOUNCE:
            circuit_notify_ts[scope_key] = now
            await try_notify_fn(msg, _CIRCUIT_OPEN_MSG)
        return

    # Fix 1: retry loop with exponential backoff for transient errors
    _backoff_delays = (1.0, 2.0, 4.0)
    _max_attempts = 1 + len(_backoff_delays)  # 1 initial + 3 retries = 4 total
    _last_exc: Exception | None = None
    _attempt = 0
    while _attempt < _max_attempts:
        try:
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
                # Drain superseded streaming (cancel-in-flight) silently.
                if outbound is not None and outbound.metadata.get("_superseded"):
                    async for _ in payload:
                        pass
                    break
                await adapter.send_streaming(msg, payload, outbound)
            if circuit is not None:
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
                and _attempt + 1 < _max_attempts
                and kind == "send"  # streaming iterators cannot be replayed
            )
            if retry_possible:
                delay = _backoff_delays[_attempt]
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
                _attempt = _max_attempts  # exit loop
                break

    # Invoke dispatched callback after send (#316).
    # "send" → payload is the OutboundMessage; else → outbound.
    _out = payload if kind == "send" else outbound
    if _out is not None:
        _dispatched = _out.metadata.pop("_on_dispatched", None)
        if callable(_dispatched):
            _dispatched_result = _dispatched(_out)
            if inspect.isawaitable(_dispatched_result):
                await _dispatched_result

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
