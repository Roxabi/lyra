"""Web smoke outbound formatter — pushes text deltas to SSE session queues."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from factory.adapters.web.web_sessions import WebSessionHub
from factory.core.messaging.message import WebMeta
from factory.outbound.formatter import BaseFormatter

if TYPE_CHECKING:
    from factory.core.messaging.render_events import (
        ReasoningDeltaRenderEvent,
        ReasoningEndRenderEvent,
        ReasoningStartRenderEvent,
    )


class WebFormatter(BaseFormatter):
    """Minimal formatter: no platform SDK — events go to WebSessionHub."""

    def __init__(self, sessions: WebSessionHub, session_id: str) -> None:
        self._sessions = sessions
        self._session_id = session_id
        self._buffer = ""

    def placeholder_text(self) -> str:
        return "…"

    def chunk(self, text: str) -> list[str]:
        return [text] if text else []

    def render_text(self, text: str) -> list[str]:
        return self.chunk(text)

    def render_buttons(self, buttons: Any) -> Any:
        del buttons
        return None

    def dim_italic(self, text: str) -> str:
        return text

    def get_msg(self, key: str, fallback: str) -> str:
        del key
        return fallback

    async def send_placeholder(self) -> tuple[Any, int | None]:
        await self._sessions.publish(self._session_id, {"type": "delta", "text": ""})
        return (self._session_id, None)

    async def edit_placeholder_text(
        self, ph: Any, text: str, *, finalize: bool = False
    ) -> None:
        del ph
        self._buffer = text
        await self._sessions.publish(self._session_id, {"type": "delta", "text": text})
        if finalize:
            # Terminal edit (graceful or error end) — close the SSE stream. This is
            # the streaming counterpart of web_outbound.send()'s "done" on the
            # non-streaming path; OutboundAdapterBase.send_streaming must NOT be
            # overridden to inject it (stage-axis invariant, ADR-073).
            await self._sessions.publish(self._session_id, {"type": "done"})

    async def send_trace_placeholder(self) -> tuple[Any, int | None]:
        return (None, None)

    async def send_message(self, text: str) -> int | None:
        await self._sessions.publish(self._session_id, {"type": "delta", "text": text})
        return None

    async def send_fallback(self, text: str) -> int | None:
        # Empty-stream / fallback terminal path (_drain_fallback) — no finalize
        # edit fires here, so close the SSE stream explicitly.
        result = await self.send_message(text)
        await self._sessions.publish(self._session_id, {"type": "done"})
        return result

    async def edit_reasoning(
        self,
        trace_obj: Any,
        event: "ReasoningStartRenderEvent | ReasoningDeltaRenderEvent | ReasoningEndRenderEvent",  # noqa: E501
    ) -> None:
        del trace_obj, event

    async def edit_tool_recap(
        self,
        trace_obj: Any,
        lines: list[str],
        done: bool,
    ) -> None:
        del trace_obj, lines, done


def web_session_id(original_msg: Any) -> str:
    """Extract browser session_id from inbound WebMeta."""
    meta = getattr(original_msg, "platform_meta", None)
    if isinstance(meta, WebMeta) and meta.session_id:
        return meta.session_id
    return getattr(original_msg, "scope_id", "unknown")
