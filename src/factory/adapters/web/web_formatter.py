"""Web smoke outbound formatter — pushes text deltas to SSE session queues."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from factory.adapters.web import web_agui
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
        self._run_id = ""
        self._message_id = ""

    def _is_agui(self) -> bool:
        return (
            self._sessions.get_or_create(self._session_id).stream_format == "agui"
        )

    async def _publish(self, event: dict[str, Any]) -> None:
        await self._sessions.publish(self._session_id, event)

    async def _publish_many(self, events: list[dict[str, Any]]) -> None:
        for event in events:
            await self._publish(event)

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
        if self._is_agui():
            self._run_id = web_agui.new_run_id()
            self._message_id = web_agui.new_message_id()
            self._buffer = ""
            await self._publish(
                web_agui.run_started(
                    thread_id=self._session_id, run_id=self._run_id
                )
            )
            await self._publish(web_agui.text_start(message_id=self._message_id))
            return (self._session_id, None)
        await self._publish({"type": "delta", "text": ""})
        return (self._session_id, None)

    async def edit_placeholder_text(
        self, ph: Any, text: str, *, finalize: bool = False
    ) -> None:
        del ph
        if self._is_agui():
            increment = text[len(self._buffer) :]
            self._buffer = text
            if increment:
                await self._publish(
                    web_agui.text_content(
                        message_id=self._message_id, delta=increment
                    )
                )
            if finalize:
                await self._publish(
                    web_agui.text_end(message_id=self._message_id)
                )
                await self._publish(
                    web_agui.run_finished(
                        thread_id=self._session_id, run_id=self._run_id
                    )
                )
                self._run_id = ""
                self._message_id = ""
                self._buffer = ""
            return
        self._buffer = text
        await self._publish({"type": "delta", "text": text})
        if finalize:
            await self._publish({"type": "done"})

    async def send_trace_placeholder(self) -> tuple[Any, int | None]:
        return (None, None)

    async def _send_agui_text(self, text: str, *, finalize: bool) -> None:
        if not self._run_id:
            self._run_id = web_agui.new_run_id()
            self._message_id = web_agui.new_message_id()
            self._buffer = ""
            await self._publish(
                web_agui.run_started(
                    thread_id=self._session_id, run_id=self._run_id
                )
            )
            await self._publish(web_agui.text_start(message_id=self._message_id))
        if text:
            await self._publish(
                web_agui.text_content(message_id=self._message_id, delta=text)
            )
            self._buffer = text
        if finalize:
            await self._publish(web_agui.text_end(message_id=self._message_id))
            await self._publish(
                web_agui.run_finished(
                    thread_id=self._session_id, run_id=self._run_id
                )
            )
            self._run_id = ""
            self._message_id = ""
            self._buffer = ""

    async def send_message(self, text: str) -> int | None:
        if self._is_agui():
            await self._send_agui_text(text, finalize=False)
            return None
        await self._publish({"type": "delta", "text": text})
        return None

    async def send_fallback(self, text: str) -> int | None:
        if self._is_agui():
            await self._send_agui_text(text, finalize=True)
            return None
        result = await self.send_message(text)
        await self._publish({"type": "done"})
        return result

    async def edit_reasoning(
        self,
        trace_obj: Any,
        event: "ReasoningStartRenderEvent | ReasoningDeltaRenderEvent | ReasoningEndRenderEvent",  # noqa: E501
    ) -> None:
        del trace_obj
        if not self._is_agui():
            return
        from factory.core.messaging.render_events import (
            ReasoningDeltaRenderEvent,
            ReasoningStartRenderEvent,
        )

        if isinstance(event, ReasoningStartRenderEvent):
            await self._publish_many(
                web_agui.reasoning_start(message_id=event.message_id)
            )
            return
        if isinstance(event, ReasoningDeltaRenderEvent):
            if event.delta:
                await self._publish(
                    web_agui.reasoning_content(
                        message_id=event.message_id, delta=event.delta
                    )
                )
            return
        # ReasoningEndRenderEvent — only remaining variant
        await self._publish_many(web_agui.reasoning_end(message_id=event.message_id))

    async def edit_tool_recap(
        self,
        trace_obj: Any,
        lines: list[str],
        done: bool,
    ) -> None:
        # Lot 3: wire ToolCallBlock via TOOL_CALL_* AG-UI events (ADR-102 deferred).
        del trace_obj, lines, done


def web_session_id(original_msg: Any) -> str:
    """Extract browser session_id from inbound WebMeta."""
    meta = getattr(original_msg, "platform_meta", None)
    if isinstance(meta, WebMeta) and meta.session_id:
        return meta.session_id
    return getattr(original_msg, "scope_id", "unknown")
