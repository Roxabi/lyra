"""RED tests for InboundContext / InboundPipeline ingest slot (#1537, #1551).

These tests will FAIL at collection (ImportError / TypeError) until the GREEN
implementation lands:
- ``InboundContext.ingest: IngestCtx | None = None`` (trailing field)
- ``InboundPipeline.__init__(..., ingest_stage=None)`` stored as ``self._ingest_stage``
- ``lyra.inbound.attachment_ingest.AttachmentIngestStage``
"""

from __future__ import annotations

from unittest.mock import MagicMock

from lyra.inbound.attachment_ingest import (  # noqa: E402 — module does not exist yet (RED)
    AttachmentIngestStage,
)

from lyra.inbound.context import DispatchCtx, InboundContext, RouterCtx, SessionCtx
from lyra.inbound.pipeline import InboundPipeline

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
