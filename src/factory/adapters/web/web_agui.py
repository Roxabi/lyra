"""AG-UI wire helpers for the web smoke adapter edge serializer."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from ag_ui.core.events import (
    BaseEvent,
    ReasoningEndEvent,
    ReasoningMessageContentEvent,
    ReasoningMessageEndEvent,
    ReasoningMessageStartEvent,
    ReasoningStartEvent,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
)

if TYPE_CHECKING:
    from factory.core.messaging.render_events import RenderEvent

StreamFormat = Literal["legacy", "agui"]

_AGUI_TERMINAL_TYPES = frozenset({"RUN_FINISHED", "RUN_ERROR"})
_LEGACY_TERMINAL_TYPES = frozenset({"done", "error"})


def agui_dict(event: BaseEvent) -> dict[str, Any]:
    """Serialize an AG-UI Pydantic event to a JSON-ready dict (camelCase keys)."""
    return event.model_dump(by_alias=True, mode="json", exclude_none=True)


def is_stream_terminal(event: dict[str, Any], stream_format: StreamFormat) -> bool:
    """Return whether *event* closes the SSE stream for *stream_format*."""
    event_type = event.get("type")
    if stream_format == "agui":
        return event_type in _AGUI_TERMINAL_TYPES
    return event_type in _LEGACY_TERMINAL_TYPES


def new_run_id() -> str:
    return uuid4().hex


def new_message_id() -> str:
    return uuid4().hex


def run_started(*, thread_id: str, run_id: str) -> dict[str, Any]:
    return agui_dict(RunStartedEvent(thread_id=thread_id, run_id=run_id))


def run_finished(*, thread_id: str, run_id: str) -> dict[str, Any]:
    return agui_dict(RunFinishedEvent(thread_id=thread_id, run_id=run_id))


def run_error(*, message: str, code: str | None = None) -> dict[str, Any]:
    return agui_dict(RunErrorEvent(message=message, code=code))


def text_start(*, message_id: str) -> dict[str, Any]:
    return agui_dict(TextMessageStartEvent(message_id=message_id, role="assistant"))


def text_content(*, message_id: str, delta: str) -> dict[str, Any]:
    return agui_dict(TextMessageContentEvent(message_id=message_id, delta=delta))


def text_end(*, message_id: str) -> dict[str, Any]:
    return agui_dict(TextMessageEndEvent(message_id=message_id))


def reasoning_start(*, message_id: str) -> list[dict[str, Any]]:
    return [
        agui_dict(ReasoningStartEvent(message_id=message_id)),
        agui_dict(
            ReasoningMessageStartEvent(message_id=message_id, role="reasoning")
        ),
    ]


def reasoning_content(*, message_id: str, delta: str) -> dict[str, Any]:
    return agui_dict(
        ReasoningMessageContentEvent(message_id=message_id, delta=delta)
    )


def reasoning_end(*, message_id: str) -> list[dict[str, Any]]:
    return [
        agui_dict(ReasoningMessageEndEvent(message_id=message_id)),
        agui_dict(ReasoningEndEvent(message_id=message_id)),
    ]


def render_event_to_agui(
    event: RenderEvent,
    *,
    thread_id: str,
) -> dict[str, Any] | list[dict[str, Any]] | None:
    """Translate a ``RenderEvent`` to AG-UI wire dict(s). Lot 3 extension point."""
    from factory.core.messaging.render_events import (
        ReasoningDeltaRenderEvent,
        ReasoningEndRenderEvent,
        ReasoningStartRenderEvent,
        RunErrorRenderEvent,
        RunFinishedRenderEvent,
        RunStartedRenderEvent,
        TextDeltaRenderEvent,
        TextEndRenderEvent,
        TextStartRenderEvent,
    )

    if isinstance(event, RunStartedRenderEvent):
        return run_started(thread_id=thread_id, run_id=event.run_id)
    if isinstance(event, RunFinishedRenderEvent):
        return run_finished(thread_id=thread_id, run_id=event.run_id)
    if isinstance(event, RunErrorRenderEvent):
        return run_error(message=event.message, code=event.code)
    if isinstance(event, TextStartRenderEvent):
        return text_start(message_id=event.message_id)
    if isinstance(event, TextDeltaRenderEvent):
        return text_content(message_id=event.message_id, delta=event.delta)
    if isinstance(event, TextEndRenderEvent):
        return text_end(message_id=event.message_id)
    if isinstance(event, ReasoningStartRenderEvent):
        return reasoning_start(message_id=event.message_id)
    if isinstance(event, ReasoningDeltaRenderEvent):
        return reasoning_content(message_id=event.message_id, delta=event.delta)
    if isinstance(event, ReasoningEndRenderEvent):
        return reasoning_end(message_id=event.message_id)
    return None
