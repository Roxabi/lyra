"""RED tests for build_ingest / _assert_prod_ingest_store wiring helpers (#1537, #1551).

Targets two functions not yet added to
``lyra.bootstrap.wiring.bootstrap_wiring``:

    def build_ingest(
        blob_store: BlobStorePort | None,
    ) -> tuple[IngestCtx, AttachmentIngestStage | None]: ...
    def _assert_prod_ingest_store(ingest: IngestCtx) -> None: ...

These tests will FAIL at collection (ImportError) until the GREEN implementation
is committed.  IngestCtx + AttachmentIngestStage are also nonexistent (RED from T1).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from lyra.bootstrap.wiring.bootstrap_wiring import (  # noqa: E402 — symbols added in GREEN
    _assert_prod_ingest_store,
    build_ingest,
)
from lyra.core.ports.blobstore import BlobStorePort
from lyra.inbound.attachment_ingest import (  # noqa: E402 — module added in GREEN
    AttachmentIngestStage,
    IngestCtx,
)


class TestBuildIngest:
    """build_ingest — factory that pairs IngestCtx with an optional stage."""

    def test_ingest_ctx_injected_with_blob_store(self) -> None:
        """blob_store provided → ctx.store is that store and stage is not None.

        Negative: if build_ingest ignores the blob_store argument and always
        sets ctx.store = None, the assertion ``ctx.store is mock_blob_store``
        fails — the ingest stage would never receive a live store in production.
        """
        # Arrange
        mock_blob_store = MagicMock(spec=BlobStorePort)

        # Act
        ctx, stage = build_ingest(mock_blob_store)

        # Assert — store is the exact object passed in
        assert ctx.store is mock_blob_store
        # A stage must be returned so the pipeline actually runs ingest
        assert stage is not None
        assert isinstance(stage, AttachmentIngestStage)

    def test_build_ingest_none_store_yields_no_stage(self) -> None:
        """blob_store=None (CLI / degraded path) → ctx.store is None, stage is None.

        Negative: if build_ingest always returns a stage regardless of the store,
        ``stage is None`` fails — a stage with no store would attempt a None.put()
        call, raising AttributeError instead of silently degrading.
        """
        # Arrange + Act
        ctx, stage = build_ingest(None)

        # Assert — degraded / CLI path: no store, no stage
        assert ctx.store is None
        assert stage is None


class TestAssertProdIngestStore:
    """_assert_prod_ingest_store — production guard that rejects None stores."""

    def test_prod_assert_raises_when_store_none(self) -> None:
        """_assert_prod_ingest_store raises AssertionError when ctx.store is None.

        Negative: if the guard ``assert ingest.store is not None`` is deleted,
        the function returns silently and the production pipeline starts with a
        None store — attachments silently degrade instead of surfacing the
        misconfiguration at startup.

        The match string ``ctx.ingest.store`` is pinned to the locked contract
        error message so that a vague assertion error is not mistaken for a pass.
        """
        # Arrange
        ingest_ctx = IngestCtx(store=None)

        # Act + Assert
        with pytest.raises(AssertionError, match="ctx.ingest.store"):
            _assert_prod_ingest_store(ingest_ctx)
