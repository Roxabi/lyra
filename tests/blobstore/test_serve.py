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
    def test_healthz_returns_200_with_status_ok(self, client: TestClient) -> None:
        """GET /healthz returns 200 and JSON body contains at least {status: ok}."""
        # Arrange — no headers required (N5: Auth = none)
        # Act
        response = client.get("/healthz")
        # Assert
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"


# ---------------------------------------------------------------------------
# N6 — GET /metrics (no auth required)
# ---------------------------------------------------------------------------


class TestMetrics:
    def test_metrics_returns_200_prometheus_text(self, client: TestClient) -> None:
        """GET /metrics returns 200 with Content-Type starting with text/plain."""
        # Arrange — no headers required (N6: Auth = none)
        # Act
        response = client.get("/metrics")
        # Assert
        assert response.status_code == 200
        content_type = response.headers.get("content-type", "")
        assert content_type.startswith("text/plain")


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
