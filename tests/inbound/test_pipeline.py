"""Tests for InboundPipeline.run — orchestration of parse→route→session→dispatch."""

from __future__ import annotations

import dataclasses
from unittest.mock import AsyncMock, MagicMock

import pytest

from factory.core.auth.trust import TrustLevel
from factory.core.messaging.message import Attachment, InboundMessage, TelegramMeta
from factory.inbound.attachment_ingest import IngestCtx, PendingAttachment
from factory.inbound.context import DispatchCtx, InboundContext, RouterCtx, SessionCtx
from factory.inbound.pipeline import InboundPipeline
from factory.inbound.router import RouteDecision

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BOT_ID = "bot-1"


def _make_msg(*, is_mention: bool = False) -> InboundMessage:
    return InboundMessage(
        id="msg-1",
        platform="telegram",
        bot_id=_BOT_ID,
        scope_id="chat:123",
        user_id="user:42",
        user_name="testuser",
        is_mention=is_mention,
        text="hello",
        text_raw="hello",
        trust_level=TrustLevel.PUBLIC,
        platform_meta=TelegramMeta(chat_id=1, is_group=False),
    )


def _make_ctx(
    *, owned_threads: set[int] | None = None, ingest: "IngestCtx | None" = None
) -> InboundContext:
    router_ctx = RouterCtx(
        bot_id=_BOT_ID,
        owned_threads=owned_threads if owned_threads is not None else set(),
        watch_channels=None,
    )
    session_ctx = SessionCtx(turn_store=None, thread_store=None)
    dispatch_ctx = MagicMock(spec=DispatchCtx)
    return InboundContext(
        router=router_ctx, session=session_ctx, dispatch=dispatch_ctx, ingest=ingest
    )


def _make_pipeline(
    *,
    route_decision: RouteDecision = RouteDecision.PROCESS,
    built_msg: InboundMessage | None = None,
) -> tuple[InboundPipeline, MagicMock, MagicMock, MagicMock]:
    """Return (pipeline, mock_router, mock_session_builder, mock_dispatcher)."""
    mock_router = MagicMock()
    mock_router.decide = MagicMock(return_value=route_decision)

    msg_after_build = built_msg if built_msg is not None else _make_msg()
    mock_session_builder = MagicMock()
    mock_session_builder.build = AsyncMock(return_value=msg_after_build)

    mock_dispatcher = MagicMock()
    mock_dispatcher.dispatch = AsyncMock(return_value=None)

    pipeline = InboundPipeline(
        router=mock_router,
        session_builder=mock_session_builder,
        dispatcher=mock_dispatcher,
    )
    return pipeline, mock_router, mock_session_builder, mock_dispatcher


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestInboundPipeline:
    """InboundPipeline.run — orchestration contract."""

    @pytest.mark.asyncio
    async def test_parser_returns_none_returns_early(self) -> None:
        """parse returns None → pipeline exits; no downstream stage is called."""
        # Arrange
        pipeline, mock_router, mock_session_builder, mock_dispatcher = _make_pipeline()
        ctx = _make_ctx()
        parser = MagicMock()
        parser.parse = MagicMock(return_value=None)
        send_backpressure = AsyncMock()
        on_drop = MagicMock()

        # Act
        await pipeline.run(
            raw="raw-event",
            ctx=ctx,
            parser=parser,
            send_backpressure=send_backpressure,
            on_drop=on_drop,
        )

        # Assert — Negative: removing `if msg is None: return` lets the pipeline
        # continue with None, causing AttributeError in router.decide.
        parser.parse.assert_called_once_with("raw-event", ctx)
        mock_router.decide.assert_not_called()
        mock_session_builder.build.assert_not_awaited()
        mock_dispatcher.dispatch.assert_not_awaited()
        on_drop.assert_not_called()

    @pytest.mark.asyncio
    async def test_route_drop_returns_early(self) -> None:
        """Router DROP: on_drop IS called; session_builder + dispatcher skipped.

        Contract (pipeline.py lines 84-87): Router-DROP is not a quiet drop;
        on_drop fires when provided.  Removing the on_drop call means typing
        indicators are never cancelled on filtered messages.
        """
        # Arrange
        pipeline, mock_router, mock_session_builder, mock_dispatcher = _make_pipeline(
            route_decision=RouteDecision.DROP,
        )
        ctx = _make_ctx()
        msg = _make_msg()
        parser = MagicMock()
        parser.parse = MagicMock(return_value=msg)
        send_backpressure = AsyncMock()
        on_drop = MagicMock()

        # Act
        await pipeline.run(
            raw="raw-event",
            ctx=ctx,
            parser=parser,
            send_backpressure=send_backpressure,
            on_drop=on_drop,
        )

        # Assert — Negative: deleting the DROP branch makes the pipeline continue into
        # session_builder.build despite the router's decision.
        mock_router.decide.assert_called_once_with(msg, ctx.router)
        mock_session_builder.build.assert_not_awaited()
        mock_dispatcher.dispatch.assert_not_awaited()
        on_drop.assert_called_once()

    @pytest.mark.asyncio
    async def test_pre_route_hook_mutates_owned_threads(self) -> None:
        """pre_route_hook that adds to owned_threads is visible after run().

        Verifies frozen-container, mutable-contents contract: the hook receives
        the same RouterCtx reference and can mutate the mutable set it carries.
        """
        # Arrange
        pipeline, _router, _, _ = _make_pipeline(
            route_decision=RouteDecision.PROCESS,
        )
        owned: set[int] = set()
        ctx = _make_ctx(owned_threads=owned)
        msg = _make_msg()
        parser = MagicMock()
        parser.parse = MagicMock(return_value=msg)
        send_backpressure = AsyncMock()

        async def _pre_route_hook(_msg: InboundMessage, _ctx: InboundContext) -> None:
            _ctx.router.owned_threads.add(123)

        # Act
        await pipeline.run(
            raw="raw-event",
            ctx=ctx,
            parser=parser,
            pre_route_hook=_pre_route_hook,
            send_backpressure=send_backpressure,
        )

        # Assert — Negative: if owned_threads were a copy instead of the shared ref,
        # ctx.router.owned_threads would still be empty after run().
        assert ctx.router.owned_threads == {123}

    @pytest.mark.asyncio
    async def test_pre_session_hook_returns_updated_msg(self) -> None:
        """pre_session_hook return value is passed to SessionBuilder.build.

        The hook returns a dataclasses.replace'd msg with is_mention=True; the
        original parsed msg has is_mention=False.  SessionBuilder must receive the
        updated msg.
        """
        # Arrange
        parsed_msg = _make_msg(is_mention=False)
        updated_msg = dataclasses.replace(parsed_msg, is_mention=True)
        # session_builder.build should receive updated_msg; make it return it
        pipeline, _router, mock_session_builder, _dispatcher = _make_pipeline(
            route_decision=RouteDecision.PROCESS,
            built_msg=updated_msg,
        )
        ctx = _make_ctx()
        parser = MagicMock()
        parser.parse = MagicMock(return_value=parsed_msg)
        send_backpressure = AsyncMock()

        async def _pre_session_hook(
            _msg: InboundMessage, _ctx: InboundContext
        ) -> InboundMessage:
            return dataclasses.replace(_msg, is_mention=True)

        # Act
        await pipeline.run(
            raw="raw-event",
            ctx=ctx,
            parser=parser,
            pre_session_hook=_pre_session_hook,
            send_backpressure=send_backpressure,
        )

        # Assert — Negative: if pipeline used `msg` (pre-hook) instead of the hook
        # return value, session_builder would receive is_mention=False.
        build_call_args = mock_session_builder.build.call_args
        assert build_call_args is not None
        received_msg: InboundMessage = build_call_args[0][0]
        assert received_msg.is_mention is True

    @pytest.mark.asyncio
    async def test_full_happy_path_calls_all_stages(self) -> None:
        """Full pipeline: parse→Router PROCESS→SessionBuilder→Dispatcher.

        All four stage methods called in order; on_drop and send_backpressure not
        invoked spuriously.
        """
        # Arrange
        parsed_msg = _make_msg()
        built_msg = _make_msg()
        pipeline, mock_router, mock_session_builder, mock_dispatcher = _make_pipeline(
            route_decision=RouteDecision.PROCESS,
            built_msg=built_msg,
        )
        ctx = _make_ctx()
        parser = MagicMock()
        parser.parse = MagicMock(return_value=parsed_msg)
        send_backpressure = AsyncMock()
        on_drop = MagicMock()

        # Act
        await pipeline.run(
            raw="raw-event",
            ctx=ctx,
            parser=parser,
            send_backpressure=send_backpressure,
            on_drop=on_drop,
        )

        # Assert — parse called with raw input
        parser.parse.assert_called_once_with("raw-event", ctx)
        # Router received parsed msg + router sub-ctx
        mock_router.decide.assert_called_once_with(parsed_msg, ctx.router)
        # SessionBuilder received parsed msg (no pre_session_hook) + session sub-ctx
        mock_session_builder.build.assert_awaited_once_with(parsed_msg, ctx.session)
        # Dispatcher received built msg + dispatch sub-ctx + callbacks
        mock_dispatcher.dispatch.assert_awaited_once_with(
            built_msg, ctx.dispatch, send_backpressure, on_drop
        )
        # on_drop not triggered by the pipeline itself (only Dispatcher may call it)
        on_drop.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_store_path_clears_pending_attachments(self) -> None:
        """No-store path (ingest_stage=None or store=None): pending_attachments cleared.

        B1 contract: when the ingest stage is skipped (no store configured), both
        singular ``pending_attachment`` and plural ``pending_attachments`` must be
        cleared before the message is forwarded to the router/dispatcher — fetch
        closures must never cross the NATS process boundary.

        Negative: if only ``pending_attachment`` is cleared (old code), a message
        carrying ``pending_attachments`` leaks live closures downstream.
        """
        # Arrange — pipeline with NO ingest stage (store=None path)
        pipeline, _mock_router, mock_session_builder, _mock_dispatcher = _make_pipeline(
            route_decision=RouteDecision.PROCESS,
        )

        # Build a message carrying a non-empty pending_attachments list
        async def _fake_fetch() -> bytes:
            return b"data"  # pragma: no cover

        pending = PendingAttachment(
            fetch=_fake_fetch,
            mime="image/jpeg",
            source="telegram",
        )
        att = Attachment(
            type="image", url_or_path_or_bytes="tg:x", mime_type="image/jpeg"
        )
        msg_with_pending = dataclasses.replace(
            _make_msg(), attachments=[att], pending_attachments=[pending]
        )

        ctx = _make_ctx(ingest=IngestCtx(store=None))
        parser = MagicMock()
        parser.parse = MagicMock(return_value=msg_with_pending)
        send_backpressure = AsyncMock()

        # Capture the msg that session_builder.build receives
        captured: list[InboundMessage] = []

        async def _capture_build(m: InboundMessage, _s: object) -> InboundMessage:
            captured.append(m)
            return m

        mock_session_builder.build = AsyncMock(side_effect=_capture_build)

        # Act
        await pipeline.run(
            raw="raw-event",
            ctx=ctx,
            parser=parser,
            send_backpressure=send_backpressure,
        )

        # Assert — the msg forwarded downstream has pending_attachments cleared
        assert len(captured) == 1
        forwarded = captured[0]
        assert forwarded.pending_attachments == [], (
            "pending_attachments must be cleared on the no-store path (B1 NATS-safety)"
        )
