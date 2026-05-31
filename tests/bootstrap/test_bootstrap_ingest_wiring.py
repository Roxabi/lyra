"""Tests for build_ingest / wire_ingest / _assert_blobstore_configured_if_url_set
wiring helpers (#1537, #1551, ADR-083).
"""

from __future__ import annotations

import types
from unittest.mock import MagicMock

import pytest

from lyra.bootstrap.wiring.bootstrap_wiring import (
    _assert_blobstore_configured_if_url_set,
    build_ingest,
    wire_ingest,
)
from lyra.core.ports.blobstore import BlobStorePort
from lyra.inbound.attachment_ingest import (
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
        mock_blob_store = MagicMock(spec=BlobStorePort)

        ctx, stage = build_ingest(mock_blob_store)

        assert ctx.store is mock_blob_store
        assert stage is not None
        assert isinstance(stage, AttachmentIngestStage)

    def test_build_ingest_none_store_yields_no_stage(self) -> None:
        """blob_store=None (CLI / degraded path) → ctx.store is None, stage is None.

        Negative: if build_ingest always returns a stage regardless of the store,
        ``stage is None`` fails — a stage with no store would attempt a None.put()
        call, raising AttributeError instead of silently degrading.
        """
        ctx, stage = build_ingest(None)

        assert ctx.store is None
        assert stage is None


class TestAssertBlobstoreConfiguredIfUrlSet:
    """_assert_blobstore_configured_if_url_set — env-signal prod guard."""

    def test_url_set_store_none_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """LYRA_BLOBSTORE_URL set + store None → RuntimeError (misconfiguration).

        Negative: if the guard is removed or the env-signal check is inverted,
        production silently no-ops instead of failing fast at startup.
        """
        monkeypatch.setenv("LYRA_BLOBSTORE_URL", "http://blobstore.example.com")
        ingest_ctx = IngestCtx(store=None)

        with pytest.raises(RuntimeError, match="LYRA_BLOBSTORE_URL"):
            _assert_blobstore_configured_if_url_set(ingest_ctx)

    def test_url_set_store_present_no_raise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """LYRA_BLOBSTORE_URL set + store present → no raise (correct config)."""
        monkeypatch.setenv("LYRA_BLOBSTORE_URL", "http://blobstore.example.com")
        mock_store = MagicMock(spec=BlobStorePort)
        ingest_ctx = IngestCtx(store=mock_store)

        # Should not raise
        _assert_blobstore_configured_if_url_set(ingest_ctx)

    def test_url_unset_store_none_no_raise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """LYRA_BLOBSTORE_URL unset + store None → no raise (honors #1540 degradation).

        Negative: if the guard fires unconditionally on store=None, dev/CLI mode
        would always error — breaking the #1540 graceful-degradation contract.
        """
        monkeypatch.delenv("LYRA_BLOBSTORE_URL", raising=False)
        ingest_ctx = IngestCtx(store=None)

        # Should not raise — degraded path is intentional
        _assert_blobstore_configured_if_url_set(ingest_ctx)


class TestWireIngest:
    """wire_ingest — single seam that injects IngestCtx and enforces prod invariant."""

    def test_wire_ingest_sets_ingest_ctx_store(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """wire_ingest(obj, store) → obj._ingest_ctx.store is store."""
        monkeypatch.delenv("LYRA_BLOBSTORE_URL", raising=False)
        mock_store = MagicMock(spec=BlobStorePort)
        adapter = types.SimpleNamespace()

        wire_ingest(adapter, mock_store)

        assert adapter._ingest_ctx.store is mock_store

    def test_wire_ingest_none_url_unset_no_raise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """wire_ingest(obj, None) with URL unset → ctx.store is None, no raise."""
        monkeypatch.delenv("LYRA_BLOBSTORE_URL", raising=False)
        adapter = types.SimpleNamespace()

        wire_ingest(adapter, None)

        assert adapter._ingest_ctx.store is None

    def test_wire_ingest_none_url_set_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """wire_ingest(obj, None) with URL set → RuntimeError (misconfiguration)."""
        monkeypatch.setenv("LYRA_BLOBSTORE_URL", "http://blobstore.example.com")
        adapter = types.SimpleNamespace()

        with pytest.raises(RuntimeError, match="LYRA_BLOBSTORE_URL"):
            wire_ingest(adapter, None)
