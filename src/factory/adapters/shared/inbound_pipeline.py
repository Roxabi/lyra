"""Shared inbound pipeline kit — resettable singleton + guarded runner (#1931)."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from factory.inbound.attachment_ingest import (
    AttachmentIngestError,
    AttachmentIngestStage,
)
from factory.inbound.context import InboundContext
from factory.inbound.dispatcher import Dispatcher
from factory.inbound.pipeline import InboundPipeline
from factory.inbound.router import Router
from factory.inbound.session_builder import SessionBuilder

log = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass
class InboundPipelineKit:
    """Process-singleton inbound stages + per-adapter parser cache."""

    pipeline: InboundPipeline
    parser_cache: dict[int, Any] = field(default_factory=dict)

    def reset(self) -> None:
        """Clear parser cache — for tests; pipeline stages are reused."""
        self.parser_cache.clear()


_kit: InboundPipelineKit | None = None


def _build_default_pipeline() -> InboundPipeline:
    dispatcher = Dispatcher()
    router = Router()
    session_builder = SessionBuilder()
    return InboundPipeline(
        router=router,
        session_builder=session_builder,
        dispatcher=dispatcher,
        ingest_stage=AttachmentIngestStage(),
    )


def get_inbound_pipeline_kit() -> InboundPipelineKit:
    """Return the process-singleton kit, creating it on first access."""
    global _kit
    if _kit is None:
        _kit = InboundPipelineKit(pipeline=_build_default_pipeline())
    return _kit


def reset_inbound_pipeline_kit() -> None:
    """Drop the singleton — for tests that need a fresh parser cache."""
    global _kit
    _kit = None


def get_or_create_parser(
    cache: dict[int, T],
    adapter: Any,
    factory: Callable[[Any], T],
) -> T:
    """Per-adapter wire parser cache keyed by adapter object id."""
    key = id(adapter)
    parser = cache.get(key)
    if parser is None:
        parser = factory(adapter)
        cache[key] = parser
    return parser


async def run_inbound_guarded(  # noqa: PLR0913 — pipeline.run kwargs surface as explicit params
    *,
    pipeline: InboundPipeline,
    raw_message: Any,
    inbound_ctx: InboundContext,
    parser: Any,
    log_context: str,
    on_attachment_ingest_error: Callable[[AttachmentIngestError], Awaitable[None]],
    pre_route_hook: Callable[..., Awaitable[None]] | None = None,
    pre_session_hook: Callable[..., Awaitable[Any]] | None = None,
    send_backpressure: Callable[[str], Awaitable[None]],
    on_drop: Callable[[], None] | None = None,
) -> None:
    """Run InboundPipeline with the always-return boundary.

    The broad except must NOT be removed — unhandled errors must NOT propagate
    to platform SDK event loops (discord.py gateway, aiogram webhook retries).
    """
    try:
        await pipeline.run(
            raw_message,
            inbound_ctx,
            parser,
            pre_route_hook=pre_route_hook,
            pre_session_hook=pre_session_hook,
            send_backpressure=send_backpressure,
            on_drop=on_drop,
        )
    except AttachmentIngestError as exc:
        await on_attachment_ingest_error(exc)
    except Exception:
        log.exception("Unhandled exception in inbound pipeline (%s)", log_context)
