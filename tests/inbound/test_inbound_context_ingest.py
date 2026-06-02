"""RED tests for InboundContext / InboundPipeline ingest slot (#1537, #1551).

These tests will FAIL at collection (ImportError / TypeError) until the GREEN
implementation lands:
- ``InboundContext.ingest: IngestCtx | None = None`` (trailing field)
- ``InboundPipeline.__init__(..., ingest_stage=None)`` stored as ``self._ingest_stage``
- ``factory.inbound.attachment_ingest.AttachmentIngestStage``
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from factory.core.audio_payload import AudioPayload
from factory.core.auth.trust import TrustLevel
from factory.core.messaging.message import InboundMessage, TelegramMeta
from factory.inbound.attachment_ingest import (  # noqa: E402 — module does not exist yet (RED)
    AttachmentIngestStage,
    IngestCtx,
    PendingAttachment,
)
from factory.inbound.context import DispatchCtx, InboundContext, RouterCtx, SessionCtx
from factory.inbound.pipeline import InboundPipeline
from factory.inbound.prebuilt_parser import PrebuiltParser

# ---------------------------------------------------------------------------
# Helpers — minimal real sub-contexts
# ---------------------------------------------------------------------------

_BOT_ID = "bot-1"


def _make_ctx() -> InboundContext:
    """Build a minimal real InboundContext (no ingest kwarg → default None)."""
    router_ctx = RouterCtx(
        bot_id=_BOT_ID,
        owned_threads=set(),
        watch_channels=None,
    )
    session_ctx = SessionCtx(turn_store=None, thread_store=None)
    dispatch_ctx = MagicMock(spec=DispatchCtx)
    return InboundContext(router=router_ctx, session=session_ctx, dispatch=dispatch_ctx)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestInboundContextIngest:
    """InboundContext.ingest — additive trailing field defaults to None."""

    def test_inbound_context_ingest_defaults_none(self) -> None:
        """Constructing InboundContext without ``ingest=`` yields ctx.ingest is None.

        Negative: if the ``ingest`` field is missing from the dataclass entirely,
        ``ctx.ingest`` raises ``AttributeError`` — the test would fail with a
        different error rather than ``AssertionError``, but it still signals RED
        correctly.  Once GREEN, the trailing field must default to None without
        requiring callers to pass it.
        """
        # Arrange + Act
        ctx = _make_ctx()

        # Assert
        assert ctx.ingest is None


class TestInboundPipelineIngestStage:
    """InboundPipeline.__init__ ingest_stage slot — additive kwarg."""

    def test_pipeline_accepts_ingest_stage_none(self) -> None:
        """Default InboundPipeline has _ingest_stage=None; explicit stage is stored.

        Two invariants:
        1. ``InboundPipeline()._ingest_stage is None`` — default is safe no-op.
        2. ``InboundPipeline(ingest_stage=AttachmentIngestStage())._ingest_stage
           is not None`` — the stage is stored for later use by pipeline.run.

        Negative for (1): if the ``ingest_stage`` kwarg is absent, the constructor
        raises ``TypeError`` — the pipeline is not additive-compatible.
        Negative for (2): if the stage is not stored on ``self._ingest_stage``, the
        pipeline silently drops it and the ingest step never runs.
        """
        # Arrange + Act
        default_pipeline = InboundPipeline()
        stage = AttachmentIngestStage()
        pipeline_with_stage = InboundPipeline(ingest_stage=stage)

        # Assert (1) — default is None
        assert default_pipeline._ingest_stage is None

        # Assert (2) — explicit stage is stored
        assert pipeline_with_stage._ingest_stage is not None


class TestInboundPipelineNoStoreClearing:
    """InboundPipeline.run no-store branch clears pending_attachment (B4 fix)."""

    async def test_pipeline_no_store_clears_pending_attachment(self) -> None:
        """No-store path: pipeline's elif branch clears the fetch closure.

        Scenario: ingest_stage is wired but ctx.ingest.store is None, so the
        stage condition (``store is not None``) is False.  The elif branch must
        fire and clear pending_attachment before the message reaches the
        dispatcher.  This proves the fetch closure never crosses the NATS
        process boundary.

        Negative: if the elif branch (B4 fix) is deleted, the dispatched message
        retains its PendingAttachment and the assertion fails — the test binds
        directly to the new behavior.
        """
        # Arrange — voice message with unresolved blob_ref + pending attachment
        pending = PendingAttachment(
            fetch=AsyncMock(return_value=b"oggbytes"),
            mime="audio/ogg",
            source="telegram",
            platform_ref="tg:file_id:XYZ",
            platform_message_id="99",
        )
        msg = InboundMessage(
            id="msg-b4",
            platform="telegram",
            bot_id="bot-1",
            scope_id="chat:1",
            user_id="user:1",
            user_name="alice",
            is_mention=False,
            text="",
            text_raw="",
            trust_level=TrustLevel.PUBLIC,
            modality="voice",
            audio=AudioPayload(
                blob_ref=None,  # unresolved until AttachmentIngestStage stamps a ref
                mime_type="audio/ogg",
                duration_ms=1000,
                file_id="XYZ",
            ),
            pending_attachment=pending,
            # TelegramMeta(is_group=False) → Router.decide returns PROCESS (DM path)
            platform_meta=TelegramMeta(is_group=False),
        )

        # InboundContext: ingest slot present but store=None (no-store path)
        router_ctx = RouterCtx(bot_id="bot-1", owned_threads=set(), watch_channels=None)
        session_ctx = SessionCtx(turn_store=None, thread_store=None)
        ingest_ctx = IngestCtx(store=None)
        dispatch_ctx = MagicMock(spec=DispatchCtx)
        ctx = InboundContext(
            router=router_ctx,
            session=session_ctx,
            dispatch=dispatch_ctx,
            ingest=ingest_ctx,
        )

        # Pipeline with a real AttachmentIngestStage (store=None → stage skipped by
        # pipeline condition; elif branch fires instead)
        pipeline = InboundPipeline(ingest_stage=AttachmentIngestStage())

        # Capture the message delivered to dispatcher.dispatch
        dispatched_msgs: list[InboundMessage] = []

        async def _capture_dispatch(
            captured_msg: InboundMessage, *_args: object, **_kwargs: object
        ) -> None:
            dispatched_msgs.append(captured_msg)

        pipeline._dispatcher = MagicMock()
        pipeline._dispatcher.dispatch = AsyncMock(side_effect=_capture_dispatch)

        # Act
        await pipeline.run(
            msg,
            ctx,
            PrebuiltParser(),
            send_backpressure=AsyncMock(),
        )

        # Assert — dispatcher received exactly one message
        assert len(dispatched_msgs) == 1, "dispatcher.dispatch must be called once"
        dispatched = dispatched_msgs[0]

        # The fetch closure must be cleared before reaching the dispatcher
        assert dispatched.pending_attachment is None, (
            "pending_attachment must be None on the dispatched message "
            "(B4 fix: elif branch clears it when store is absent)"
        )
