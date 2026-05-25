"""Conformance tests for the `BlobStore` Protocol + `BlobRef` envelope.

Verifies the shape contract from ADR-067 §Interface and the V1 spec.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime

import pytest

from roxabi_blobs import (
    BlobConsistencyError,
    BlobError,
    BlobNotFoundError,
    BlobRef,
    BlobStateError,
    BlobStore,
    BlobWriteError,
)


def _utc(year: int = 2026, month: int = 5, day: int = 21) -> datetime:
    return datetime(year, month, day, tzinfo=UTC)


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
        "id",
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
            created_at=_utc(),
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
                created_at=_utc(),
            )

    def test_frozen(self) -> None:
        ref = BlobRef(
            store_key="x",
            content_hash="x",
            mime="x",
            size=0,
            source="x",
            created_at=_utc(),
        )
        with pytest.raises(Exception):
            ref.size = 99  # frozen — must raise

    def test_created_at_rejects_naive(self) -> None:
        """tz-naive datetime is rejected (consensus W5)."""
        with pytest.raises(ValueError, match="timezone-aware"):
            BlobRef(
                store_key="x",
                content_hash="x",
                mime="x",
                size=0,
                source="x",
                created_at=datetime(2026, 5, 21),  # naive
            )


class TestTypedErrors:
    """All package errors inherit from `BlobError` so callers can catch one base."""

    @pytest.mark.parametrize(
        "exc",
        [
            BlobNotFoundError,
            BlobWriteError,
            BlobConsistencyError,
            BlobStateError,
        ],
    )
    def test_subclass_of_blob_error(self, exc: type[Exception]) -> None:
        """consensus S2 — assert against `BlobError`, not `Exception` (tautology)."""
        assert issubclass(exc, BlobError)

    def test_blob_error_catchable(self) -> None:
        """All typed errors can be caught uniformly via `BlobError`."""
        types = (
            BlobNotFoundError,
            BlobWriteError,
            BlobConsistencyError,
            BlobStateError,
        )
        for cls in types:
            with pytest.raises(BlobError):
                raise cls("test")
