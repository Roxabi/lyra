"""Adapter-side typing shim — pub/sub vs legacy TypingTaskManager (#1931)."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from factory.core.trace import TraceContext
from factory.transport.typing_publisher import is_typing_enabled
from factory.typing.listener import typing_publisher_shim
from factory.typing.types import FactoryBuilder


def start_typing_shim(  # noqa: PLR0913 — mirrors typing_publisher_shim arity (#1377)
    *,
    platform: str,
    bot_id: str,
    scope_id: int,
    typing_manager: Any,
    factory_builder: FactoryBuilder,
    typing_publisher: Any | None,
) -> None:
    """Start typing — pub/sub path when enabled, else legacy TypingTaskManager."""
    if typing_publisher is not None and typing_publisher_shim(
        platform,
        bot_id,
        scope_id,
        typing_publisher,
        typing_publisher.publish_started,
        trace_id=TraceContext.get_trace_id() or uuid4().hex,
    ):
        return
    if is_typing_enabled():
        return  # pub/sub active but publisher absent → intentional no-op
    typing_manager.start(scope_id, factory_builder(scope_id))


def cancel_typing_shim(
    *,
    platform: str,
    bot_id: str,
    scope_id: int,
    typing_manager: Any,
    typing_publisher: Any | None,
) -> None:
    """Cancel typing — pub/sub path when enabled, else legacy TypingTaskManager."""
    if typing_publisher is not None and typing_publisher_shim(
        platform,
        bot_id,
        scope_id,
        typing_publisher,
        typing_publisher.publish_ended,
        trace_id=TraceContext.get_trace_id() or uuid4().hex,
    ):
        return
    if is_typing_enabled():
        return  # pub/sub active but publisher absent → intentional no-op
    typing_manager.cancel(scope_id)
