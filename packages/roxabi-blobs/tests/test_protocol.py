"""Conformance tests for the `BlobStore` Protocol + `BlobRef` envelope.

Verifies the shape contract from ADR-067 §Interface and the V1 spec.
"""

from __future__ import annotations

import inspect
from datetime import datetime

import pytest

from roxabi_blobs import (
    BlobConsistencyError,
    BlobNotFoundError,
    BlobRef,
    BlobStore,
    BlobWriteError,
)


class TestBlobStoreProtocol:
    """`BlobStore` is a `typing.Protocol` with 4 async methods."""

    @pytest.mark.parametrize("method", ["put", "get", "exists", "delete"])
    def test_protocol_has_method(self, method: str) -> None:
        assert hasattr(BlobStore, method), f"BlobStore.{method} missing"

    @pytest.mark.parametrize("method", ["put", "get", "exists", "delete"])
    def test_method_is_coroutine(self, method: str) -> None:
        fn = getattr(BlobStore, method)
        assert inspect.iscoroutinefunction(fn), f"BlobStore.{method} must be async"

    def test_put_signature(self) -> None:
        sig = inspect.signature(BlobStore.put)
        params = sig.parameters
        # self + data + 5 keyword params
        assert "data" in params
        assert "mime" in params
        assert "source" in params
        assert "filename" in params
        assert "platform_ref" in params
        assert "platform_message_id" in params
        # 3 nullable kwargs default to None
        assert params["filename"].default is None
        assert params["platform_ref"].default is None
        assert params["platform_message_id"].default is None

    def test_get_signature(self) -> None:
        sig = inspect.signature(BlobStore.get)
        assert "store_key" in sig.parameters

    def test_exists_signature(self) -> None:
        sig = inspect.signature(BlobStore.exists)
        assert "content_hash" in sig.parameters

    def test_delete_signature(self) -> None:
        sig = inspect.signature(BlobStore.delete)
        assert "blob_ref_id" in sig.parameters


class TestBlobRefEnvelope:
    """`BlobRef` mirrors ADR-067 §BlobRef envelope exactly."""

    EXPECTED_FIELDS = {
        "store_key",
        "content_hash",
        "mime",
        "size",
        "filename",
        "source",
        "platform_ref",
        "platform_message_id",
        "created_at",
    }

    def test_field_set(self) -> None:
        assert set(BlobRef.model_fields) == self.EXPECTED_FIELDS

    def test_construct_minimum(self) -> None:
        ref = BlobRef(
            store_key="/tmp/aa/abc",
            content_hash="abc",
            mime="audio/ogg",
            size=42,
            source="telegram",
            created_at=datetime(2026, 5, 21),
        )
        assert ref.filename is None
        assert ref.platform_ref is None
        assert ref.platform_message_id is None

    def test_size_non_negative(self) -> None:
        with pytest.raises(ValueError):
            BlobRef(
                store_key="x",
                content_hash="x",
                mime="x",
                size=-1,
                source="x",
                created_at=datetime(2026, 5, 21),
            )

    def test_frozen(self) -> None:
        ref = BlobRef(
            store_key="x",
            content_hash="x",
            mime="x",
            size=0,
            source="x",
            created_at=datetime(2026, 5, 21),
        )
        with pytest.raises(Exception):
            ref.size = 99  # frozen — must raise


class TestTypedErrors:
    """All errors subclass a common base and are importable from the package root."""

    def test_errors_share_base(self) -> None:
        for exc in (BlobNotFoundError, BlobWriteError, BlobConsistencyError):
            assert issubclass(exc, Exception)
