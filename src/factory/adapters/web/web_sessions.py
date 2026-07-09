"""In-memory SSE session queues for the web smoke adapter."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from factory.adapters.web.web_agui import StreamFormat, is_stream_terminal


@dataclass
class WebSession:
    """Per-browser-session outbound event queue."""

    queue: asyncio.Queue[dict[str, Any]] = field(
        default_factory=lambda: asyncio.Queue(maxsize=512)
    )
    closed: bool = False
    stream_format: StreamFormat = "legacy"


class WebSessionHub:
    """Registry of live browser sessions keyed by session_id."""

    def __init__(self) -> None:
        self._sessions: dict[str, WebSession] = {}

    def get_or_create(self, session_id: str) -> WebSession:
        session = self._sessions.get(session_id)
        if session is None:
            session = WebSession()
            self._sessions[session_id] = session
        return session

    def set_stream_format(self, session_id: str, stream_format: StreamFormat) -> None:
        self.get_or_create(session_id).stream_format = stream_format

    def close(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session is not None:
            session.closed = True

    async def publish(self, session_id: str, event: dict[str, Any]) -> None:
        session = self.get_or_create(session_id)
        if session.closed:
            return
        try:
            session.queue.put_nowait(event)
        except asyncio.QueueFull:
            # Terminal events (done/error) must never be dropped — the SSE
            # generator only exits when it dequeues one. Evict the oldest queued
            # item to make room. Non-terminal events (deltas/pings) may drop.
            if is_stream_terminal(event, session.stream_format):
                try:
                    session.queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    session.queue.put_nowait(event)
                except asyncio.QueueFull:
                    pass
