"""RED-phase tests for V2 HTTP API (N1–N4) and stale-token assertion (SC-Code-5)."""

from __future__ import annotations

import pathlib

import pytest
from fastapi.testclient import TestClient

from lyra.blobstore.serve import build_app

# Small PNG-like payload for PUT tests.
_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"x" * 100


def _make_client(token_path: pathlib.Path, blob_root: pathlib.Path) -> TestClient:
    """Build a TestClient against the future build_app(token_path, blob_root) API.

    Enters the client context manager so the ASGI lifespan (FsBlobStore open)
    is triggered before the first request.
    """
    app = build_app(token_path=token_path, blob_root=blob_root)
    client = TestClient(app)
    client.__enter__()
    return client


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def token_path(tmp_path: pathlib.Path) -> pathlib.Path:
    """Write 'test-token' to a temp file and return its path."""
    p = tmp_path / "blobstore.tok"
    p.write_text("test-token")
    return p


@pytest.fixture()
def blob_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """Return a fresh temp dir for blob storage."""
    d = tmp_path / "blobs"
    d.mkdir()
    return d


@pytest.fixture()
def client(token_path: pathlib.Path, blob_root: pathlib.Path):  # type: ignore[return]
    """TestClient wired with token_path + blob_root."""
    app = build_app(token_path=token_path, blob_root=blob_root)
    with TestClient(app) as c:
        yield c


def _auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}


def _put_headers() -> dict[str, str]:
    return {
        **_auth_headers(),
        "Content-Type": "image/png",
        "X-Blob-Source": "test-source",
        "X-Blob-Platform-Ref": "test-ref",
        "X-Blob-Platform-Message-Id": "test-msg-id",
        "X-Blob-Filename": "test.png",
    }


# ---------------------------------------------------------------------------
# N1 — PUT /blobs
# ---------------------------------------------------------------------------


class TestPutBlob:
    def test_put_returns_200_with_blob_ref_on_valid_bearer(
        self, client: TestClient
    ) -> None:
        """PUT /blobs with valid bearer returns 200 and a BlobRef-shaped JSON body."""
        # Arrange
        headers = _put_headers()
        # Act
        response = client.put("/blobs", content=_PNG_BYTES, headers=headers)
        # Assert
        assert response.status_code == 200
        body = response.json()
        assert "store_key" in body
        assert "content_hash" in body
        assert "size" in body
        assert "mime" in body

    def test_put_returns_401_on_wrong_bearer(
        self, client: TestClient
    ) -> None:
        """PUT /blobs with wrong bearer returns 401."""
        # Arrange
        headers = {
            **_put_headers(),
            "Authorization": "Bearer wrong-token",
        }
        # Act
        response = client.put("/blobs", content=_PNG_BYTES, headers=headers)
        # Assert
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# N2 — GET /blobs/{store_key}
# ---------------------------------------------------------------------------


class TestGetBlob:
    def test_get_returns_200_with_body_after_put(
        self, client: TestClient
    ) -> None:
        """PUT then GET the returned store_key; body matches uploaded bytes."""
        # Arrange — PUT first
        put_resp = client.put("/blobs", content=_PNG_BYTES, headers=_put_headers())
        assert put_resp.status_code == 200
        store_key = put_resp.json()["store_key"]
        # Act
        response = client.get(f"/blobs/{store_key}", headers=_auth_headers())
        # Assert
        assert response.status_code == 200
        assert response.content == _PNG_BYTES

    def test_get_returns_404_when_unknown_store_key(
        self, client: TestClient
    ) -> None:
        """GET on a never-PUT store_key returns 404."""
        # Arrange
        # Act
        response = client.get("/blobs/nonexistent-key", headers=_auth_headers())
        # Assert
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# N3 — HEAD /blobs/{store_key}
# ---------------------------------------------------------------------------


class TestHeadBlob:
    def test_head_returns_200_when_exists(
        self, client: TestClient
    ) -> None:
        """HEAD /blobs/{store_key} returns 200 with no body after a PUT."""
        # Arrange
        put_resp = client.put("/blobs", content=_PNG_BYTES, headers=_put_headers())
        assert put_resp.status_code == 200
        store_key = put_resp.json()["store_key"]
        # Act
        response = client.head(f"/blobs/{store_key}", headers=_auth_headers())
        # Assert
        assert response.status_code == 200
        assert response.content == b""

    def test_head_returns_404_when_unknown(
        self, client: TestClient
    ) -> None:
        """HEAD /blobs/{store_key} returns 404 for an unknown key."""
        # Arrange
        # Act
        response = client.head("/blobs/nonexistent-key", headers=_auth_headers())
        # Assert
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# N4 — DELETE /blobs/{store_key}
# ---------------------------------------------------------------------------


class TestDeleteBlob:
    def test_delete_returns_204_when_exists(
        self, client: TestClient
    ) -> None:
        """DELETE returns 204; subsequent HEAD returns 404."""
        # Arrange
        put_resp = client.put("/blobs", content=_PNG_BYTES, headers=_put_headers())
        assert put_resp.status_code == 200
        store_key = put_resp.json()["store_key"]
        # Act — delete
        del_resp = client.delete(f"/blobs/{store_key}", headers=_auth_headers())
        # Assert — delete succeeded
        assert del_resp.status_code == 204
        # Assert — subsequent HEAD shows key is gone
        head_resp = client.head(f"/blobs/{store_key}", headers=_auth_headers())
        assert head_resp.status_code == 404

    def test_delete_returns_404_when_unknown(
        self, client: TestClient
    ) -> None:
        """DELETE on an unknown store_key returns 404."""
        # Arrange
        # Act
        response = client.delete("/blobs/nonexistent-key", headers=_auth_headers())
        # Assert
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# SC-Code-5 — Stale-token two-direction assertion
# ---------------------------------------------------------------------------


class TestStaleToken:
    def test_token_is_read_once_at_startup(self, tmp_path: pathlib.Path) -> None:
        """Token is read once at startup; changing the file has no effect.

        Two-direction assertion (SC-Code-5):
          (a) OLD token still passes after file is overwritten with NEW.
          (b) NEW token (what is currently on disk) is rejected.
        Both branches are required — OLD-passes alone proves nothing about re-reads.
        """
        # Arrange — write OLD token to file, boot app
        tok_file = tmp_path / "blobstore.tok"
        tok_file.write_text("OLD")
        blob_root = tmp_path / "blobs"
        blob_root.mkdir()
        client = _make_client(tok_file, blob_root)

        # Sanity: OLD passes before overwrite
        pre_resp = client.get(
            "/blobs/anything", headers={"Authorization": "Bearer OLD"}
        )
        # We only care about non-401 here (could be 404 once handlers exist)
        assert pre_resp.status_code != 401

        # Overwrite the file with NEW
        tok_file.write_text("NEW")

        # Act + Assert (a): OLD still passes — token was cached at startup
        old_resp = client.get(
            "/blobs/anything", headers={"Authorization": "Bearer OLD"}
        )
        assert old_resp.status_code != 401, (
            "OLD token should still be accepted after file overwrite "
            "(token must be read once at startup, not per-request)"
        )

        # Act + Assert (b): NEW is rejected — on disk but NOT the startup value
        new_resp = client.get(
            "/blobs/anything", headers={"Authorization": "Bearer NEW"}
        )
        assert new_resp.status_code == 401, (
            "NEW token (current file content) must be rejected "
            "(server must not re-read the token file)"
        )
