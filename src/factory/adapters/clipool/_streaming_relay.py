"""Streaming chunk relay for CliPoolNatsWorker."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from factory.adapters.clipool._worker_helpers import _make_chunk
from factory.core.messaging.events import ResultLlmEvent, TextLlmEvent, ToolUseLlmEvent

ReplyFn = Callable[[Any, bytes], Awaitable[None]]


async def relay_streaming_events(
    *,
    iterator: AsyncIterator[Any],
    pool_id: str,
    resumed: bool | None,
    msg: Any,
    reply: ReplyFn,
) -> None:
    """Map pool streaming LlmEvents to CliChunkEvent replies on *msg*."""
    first_chunk = True
    async for event in iterator:
        resume_extra: dict = {"resumed": resumed} if first_chunk else {}
        first_chunk = False
        if isinstance(event, TextLlmEvent):
            chunk = _make_chunk(
                pool_id,
                event_type="text",
                text=event.text,
                done=False,
                **resume_extra,
            )
            await reply(msg, chunk)
        elif isinstance(event, ToolUseLlmEvent):
            chunk = _make_chunk(
                pool_id,
                event_type="tool_use",
                tool_name=event.tool_name,
                tool_id=event.tool_id,
                tool_input=event.input,
                done=False,
                **resume_extra,
            )
            await reply(msg, chunk)
        elif isinstance(event, ResultLlmEvent):
            chunk = _make_chunk(
                pool_id,
                event_type="result",
                is_error=event.is_error,
                session_id=event.session_id or None,
                done=True,
                worker_error=event.worker_error,
            )
            await reply(msg, chunk)
            return
    await reply(msg, _make_chunk(pool_id, event_type="result", done=True))