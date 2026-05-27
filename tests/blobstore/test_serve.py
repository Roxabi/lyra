"""RED-phase tests for lyra.blobstore.serve — skeleton boot (N5, N6, S1 auth)."""

from __future__ import annotations

import pathlib

import pytest
from fastapi.testclient import TestClient

from lyra.blobstore.serve import build_app

# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path: pathlib.Path):  # type: ignore[return]
    """Synchronous TestClient for build_app with a fixed test token."""
    tok = tmp_path / "blobstore.tok"
    tok.write_text("test-token")
    blob_root = tmp_path / "blobs"
    blob_root.mkdir()
    app = build_app(token_path=tok, blob_root=blob_root)
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# N5 — GET /healthz (no auth required)
# ---------------------------------------------------------------------------


class TestHealthz:
    def test_healthz_returns_200_with_full_body(self, client: TestClient) -> None:
        """GET /healthz returns 200 with status, disk_used_pct, and blob_count."""
        # Arrange — no headers required (N5: Auth = none)
        # Act
        response = client.get("/healthz")
        # Assert
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        # SC-Obs-1: disk_used_pct + blob_count required (None acceptable on failure)
        assert "disk_used_pct" in body
        assert "blob_count" in body
        if body["disk_used_pct"] is not None:
            assert isinstance(body["disk_used_pct"], (int, float))
            assert 0.0 <= float(body["disk_used_pct"]) <= 100.0
        if body["blob_count"] is not None:
            assert isinstance(body["blob_count"], int)
            assert body["blob_count"] >= 0


# ---------------------------------------------------------------------------
# N6 — GET /metrics (no auth required)
# ---------------------------------------------------------------------------


class TestMetrics:
    def test_metrics_returns_200_with_disk_used_pct_gauge(
        self, client: TestClient
    ) -> None:
        """GET /metrics returns 200 with blobstore_up, disk_used_pct, blob_count."""
        # Arrange — no headers required (N6: Auth = none)
        # Act
        response = client.get("/metrics")
        # Assert
        assert response.status_code == 200
        content_type = response.headers.get("content-type", "")
        assert content_type.startswith("text/plain")
        body = response.text
        # SC-Obs-2: required gauges
        assert "blobstore_up" in body
        assert "blobstore_disk_used_pct" in body
        assert "blobstore_blob_count" in body


# ---------------------------------------------------------------------------
# S1 — BearerAuthMiddleware fires on protected paths
# ---------------------------------------------------------------------------


class TestBearerAuth:
    def test_protected_endpoint_returns_401_without_bearer(
        self, client: TestClient
    ) -> None:
        """GET /blobs/<store_key> returns 401 when Authorization header is absent."""
        # Arrange — no Authorization header
        # Act
        response = client.get("/blobs/anything-store-key")
        # Assert
        assert response.status_code == 401
