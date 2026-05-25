"""Cross-host integration test for HttpBlobStore over Tailnet (M1 only)."""

from __future__ import annotations

from pathlib import Path

import pytest

from .conftest import on_m1


@pytest.mark.integration
@pytest.mark.skipif(not on_m1(), reason="cross-host path requires Tailnet on M1")
async def test_http_blob_store_against_tailnet_m1() -> None:
    """E2E: HttpBlobStore over Tailnet against a live lyra-blobstore service on M1."""
    from roxabi_blobs import HttpBlobStore

    # Arrange
    token = (Path.home() / ".lyra" / "blobstore.tok").read_text().strip()
    payload = b"lyra-blobstore-cross-host-smoke"

    async with HttpBlobStore(
        base_url="http://roxabituwer.goose-logarithm.ts.net:8449",
        token=token,
    ) as store:
        # Act
        ref = await store.put(
            payload, mime="application/octet-stream", source="test:cross_host"
        )
        retrieved = await store.get(ref.store_key)

    # Assert
    assert retrieved == payload
