"""Dispatch functions extracted from Hub — response, streaming, attachment, audio."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from .message import (
    InboundMessage,
    OutboundAttachment,
    OutboundAudio,
    OutboundAudioChunk,
    OutboundMessage,
    Platform,
    Response,
)
from .message_pipeline import Action, PipelineResult

if TYPE_CHECKING:
    from .hub import Hub

log = logging.getLogger(__name__)


async def circuit_breaker_drop(hub: Hub, msg: InboundMessage) -> bool:
    """Return True if the circuit is open and a fast-fail reply was sent.

    Internal API — promoted from private for cross-module access by
    MessagePipeline. Not part of the public Hub contract.
    """
    if hub.circuit_registry is None:
        return False
    cb = hub.circuit_registry.get("anthropic")
    if cb is None or not cb.is_open():
        return False
    status = cb.get_status()
    retry_secs = int(status.retry_after or 0)
    _retry_str = str(retry_secs)
    _unavail = (
        hub._msg_manager.get("unavailable", retry_secs=_retry_str)
        if hub._msg_manager
        else f"Lyra is currently unavailable. Please try again in {retry_secs}s."
    )
    try:
        await dispatch_response(hub, msg, Response(content=_unavail))
    except Exception as exc:
        log.exception("dispatch_response failed for fast-fail reply: %s", exc)
    return True


async def dispatch_response(
    hub: Hub, msg: InboundMessage, response: Response | OutboundMessage
) -> None:
    """Send response back via the originating adapter.

    Routes through the OutboundDispatcher when one is registered for the
    platform (fire-and-forget queue). Falls back to a direct adapter call
    when no dispatcher is registered (used in tests and command responses).

    Accepts either a Response (backward compat) or OutboundMessage directly.
    """
    if isinstance(response, OutboundMessage):
        outbound = response
    else:
        outbound = response.to_outbound()
    if outbound.routing is None and msg.routing is not None:
        outbound.routing = msg.routing
    platform = Platform(msg.platform)
    dispatcher = hub.outbound_dispatchers.get((platform, msg.bot_id))
    if dispatcher is not None:
        dispatcher.enqueue(msg, outbound)
        hub._last_processed_at = time.monotonic()
    else:
        # Fallback: direct adapter call (backward compat / no dispatcher registered)
        adapter = hub.adapter_registry.get((platform, msg.bot_id))
        if adapter is None:
            raise KeyError(
                f"No adapter registered for ({msg.platform!r}, {msg.bot_id!r}). "
                "Call register_adapter() before dispatching responses."
            )
        await adapter.send(msg, outbound)
        hub._last_processed_at = time.monotonic()

    # /voice command: dispatch audio carried in Response.audio (pool path)
    if isinstance(response, Response) and response.audio:
        await dispatch_audio(hub, msg, response.audio)

    # Voice modality: synthesize TTS audio in background after text is dispatched
    _should_speak = msg.modality == "voice" or (
        isinstance(response, Response) and response.speak
    )
    if _should_speak and hub._tts is not None:
        text = outbound.to_text().strip()
        if text:
            task = asyncio.create_task(
                hub._audio_pipeline.synthesize_and_dispatch_audio(msg, text),
                name=f"tts:{msg.id}",
            )
            hub._memory_tasks.add(task)
            task.add_done_callback(hub._memory_tasks.discard)


async def dispatch_streaming(
    hub: Hub,
    msg: InboundMessage,
    chunks: AsyncIterator[str],
    outbound: OutboundMessage | None = None,
) -> None:
    """Stream response back via the originating adapter.

    Routes through the OutboundDispatcher when one is registered (fire-and-forget).
    Falls back to a direct adapter call when no dispatcher is registered.

    When *outbound* is provided it is forwarded to the adapter so the
    platform message ID can be recorded in ``outbound.metadata``.

    Voice modality: accumulates the full stream then delegates to
    dispatch_response() which handles both text and TTS dispatch.
    """
    # Voice modality: accumulate full stream, dispatch text + schedule TTS
    if msg.modality == "voice" and hub._tts is not None:
        text_parts: list[str] = []
        async for chunk in chunks:
            text_parts.append(chunk)
        full_text = "".join(text_parts)
        if full_text.strip():
            await dispatch_response(hub, msg, Response(content=full_text))
        return

    if (
        outbound is not None
        and outbound.routing is None
        and msg.routing is not None
    ):
        outbound.routing = msg.routing
    platform = Platform(msg.platform)
    dispatcher = hub.outbound_dispatchers.get((platform, msg.bot_id))
    if dispatcher is not None:
        dispatcher.enqueue_streaming(msg, chunks, outbound)
        hub._last_processed_at = time.monotonic()
        return
    # Fallback: direct adapter call (backward compat / no dispatcher registered)
    adapter = hub.adapter_registry.get((platform, msg.bot_id))
    if adapter is None:
        raise KeyError(
            f"No adapter registered for ({msg.platform!r}, {msg.bot_id!r}). "
            "Call register_adapter() before dispatching responses."
        )
    if hasattr(adapter, "send_streaming"):
        await adapter.send_streaming(msg, chunks, outbound)
    else:
        # Fallback: accumulate and send as one message
        if outbound is not None:
            log.warning(
                "Adapter for %s lacks send_streaming; "
                "reply_message_id will not be recorded",
                msg.platform,
            )
        text = ""
        async for chunk in chunks:
            text += chunk
        await adapter.send(msg, OutboundMessage.from_text(text))
    hub._last_processed_at = time.monotonic()


async def dispatch_attachment(
    hub: Hub, msg: InboundMessage, attachment: OutboundAttachment
) -> None:
    """Send an attachment back via the originating adapter.

    Routes through the OutboundDispatcher when one is registered (fire-and-forget).
    Falls back to a direct adapter call when no dispatcher is registered.
    """
    platform = Platform(msg.platform)
    dispatcher = hub.outbound_dispatchers.get((platform, msg.bot_id))
    if dispatcher is not None:
        dispatcher.enqueue_attachment(msg, attachment)
        hub._last_processed_at = time.monotonic()
        return
    # Fallback: direct adapter call (backward compat / no dispatcher registered)
    adapter = hub.adapter_registry.get((platform, msg.bot_id))
    if adapter is None:
        raise KeyError(
            f"No adapter registered for ({msg.platform!r}, {msg.bot_id!r}). "
            "Call register_adapter() before dispatching attachments."
        )
    await adapter.render_attachment(attachment, msg)
    hub._last_processed_at = time.monotonic()


async def dispatch_audio(
    hub: Hub, msg: InboundMessage, audio: OutboundAudio
) -> None:
    """Send an audio voice note back via the originating adapter.

    Routes through the OutboundDispatcher when one is registered (fire-and-forget).
    Falls back to a direct adapter call when no dispatcher is registered.
    """
    platform = Platform(msg.platform)
    dispatcher = hub.outbound_dispatchers.get((platform, msg.bot_id))
    if dispatcher is not None:
        dispatcher.enqueue_audio(msg, audio)
        hub._last_processed_at = time.monotonic()
        return
    # Fallback: direct adapter call (backward compat / no dispatcher registered)
    adapter = hub.adapter_registry.get((platform, msg.bot_id))
    if adapter is None:
        raise KeyError(
            f"No adapter registered for ({msg.platform!r}, {msg.bot_id!r}). "
            "Call register_adapter() before dispatching audio."
        )
    await adapter.render_audio(audio, msg)
    hub._last_processed_at = time.monotonic()


async def dispatch_audio_stream(
    hub: Hub,
    msg: InboundMessage,
    chunks: AsyncIterator[OutboundAudioChunk],
) -> None:
    """Stream audio chunks back via the originating adapter.

    Routes through the OutboundDispatcher when one is registered (fire-and-forget).
    Falls back to a direct adapter call when no dispatcher is registered.
    """
    platform = Platform(msg.platform)
    dispatcher = hub.outbound_dispatchers.get((platform, msg.bot_id))
    if dispatcher is not None:
        dispatcher.enqueue_audio_stream(msg, chunks)
        hub._last_processed_at = time.monotonic()
        return
    # Fallback: direct adapter call (backward compat / no dispatcher registered)
    adapter = hub.adapter_registry.get((platform, msg.bot_id))
    if adapter is None:
        raise KeyError(
            f"No adapter registered for ({msg.platform!r}, {msg.bot_id!r}). "
            "Call register_adapter() before dispatching audio stream."
        )
    await adapter.render_audio_stream(chunks, msg)
    hub._last_processed_at = time.monotonic()


async def dispatch_voice_stream(
    hub: Hub,
    msg: InboundMessage,
    chunks: AsyncIterator[OutboundAudioChunk],
) -> None:
    """Stream TTS audio to an active Discord voice session.

    Routes through the OutboundDispatcher when one is registered (fire-and-forget).
    Falls back to a direct adapter call when no dispatcher is registered.
    """
    platform = Platform(msg.platform)
    dispatcher = hub.outbound_dispatchers.get((platform, msg.bot_id))
    if dispatcher is not None:
        dispatcher.enqueue_voice_stream(msg, chunks)
        hub._last_processed_at = time.monotonic()
        return
    # Fallback: direct adapter call (backward compat / no dispatcher registered)
    adapter = hub.adapter_registry.get((platform, msg.bot_id))
    if adapter is None:
        raise KeyError(
            f"No adapter registered for ({msg.platform!r}, {msg.bot_id!r}). "
            "Call register_adapter() before dispatching voice stream."
        )
    await adapter.render_voice_stream(chunks, msg)
    hub._last_processed_at = time.monotonic()


async def dispatch_pipeline_result(
    hub: Hub, msg: InboundMessage, result: PipelineResult
) -> None:
    """Dispatch a pipeline result: send command response or submit to pool."""
    if result.action == Action.COMMAND_HANDLED:
        if result.response and (result.response.content or result.response.audio):
            if result.response.audio:
                try:
                    await hub.dispatch_audio(msg, result.response.audio)
                except Exception as exc:
                    log.exception("dispatch_audio() failed: %s", exc)
            if result.response.content:
                try:
                    await hub.dispatch_response(msg, result.response)
                except Exception as exc:
                    log.exception("dispatch_response() failed: %s", exc)
        else:
            log.debug(
                "command returned empty response for msg id=%s — skipping dispatch",
                msg.id,
            )
    elif result.action == Action.SUBMIT_TO_POOL and result.pool:
        result.pool.submit(msg)
