"""Blobstore singleton tests (ported from voiceCLI test_blobs_factory)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

import pytest

from roxabi_satellite import blobs


@dataclass
class _FakeHttpBlobStore:
    base_url: str
    token: str


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    monkeypatch.setattr(blobs, "_INSTANCE", None)
    monkeypatch.delenv("BLOBSTORE_BEARER_TOKEN_PATH", raising=False)
    monkeypatch.delenv("BLOBSTORE_URL", raising=False)
    monkeypatch.delenv("BLOBSTORE_BACKEND", raising=False)
    monkeypatch.delenv("FACTORY_BLOBSTORE_URL", raising=False)
    monkeypatch.delenv("FACTORY_BLOBSTORE_TOKEN_PATH", raising=False)
    yield
    monkeypatch.setattr(blobs, "_INSTANCE", None)


@pytest.fixture()
def _fake_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(blobs, "HttpBlobStore", _FakeHttpBlobStore)


def test_singleton_caches(monkeypatch: pytest.MonkeyPatch, _fake_store: None) -> None:
    monkeypatch.setenv("BLOBSTORE_BACKEND", "http")
    monkeypatch.setenv("BLOBSTORE_URL", "http://hub:9000")
    monkeypatch.setenv("BLOBSTORE_BEARER_TOKEN", "tok")
    a = blobs.get_blobstore()
    b = blobs.get_blobstore()
    assert a is b


def test_flat_fs_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BLOBSTORE_BACKEND", "flat-fs")
    with pytest.raises(blobs.BlobstoreConfigError, match="ADR-068"):
        blobs.get_blobstore()


def test_missing_blobstore_url(monkeypatch: pytest.MonkeyPatch, _fake_store: None) -> None:
    monkeypatch.setenv("BLOBSTORE_BACKEND", "http")
    monkeypatch.delenv("BLOBSTORE_URL", raising=False)
    monkeypatch.setenv("BLOBSTORE_BEARER_TOKEN", "tok")
    with pytest.raises(blobs.BlobstoreConfigError, match="BLOBSTORE_URL"):
        blobs.get_blobstore()


def test_bearer_token_path_preferred(
    monkeypatch: pytest.MonkeyPatch, _fake_store: None, tmp_path: Path
) -> None:
    tok_file = tmp_path / "blobstore.tok"
    tok_file.write_text("from-file-token\n", encoding="utf-8")
    monkeypatch.setenv("BLOBSTORE_BACKEND", "http")
    monkeypatch.setenv("BLOBSTORE_URL", "http://hub:8080")
    monkeypatch.setenv("BLOBSTORE_BEARER_TOKEN", "from-env")
    monkeypatch.setenv("BLOBSTORE_BEARER_TOKEN_PATH", str(tok_file))
    store = blobs.get_blobstore()
    assert isinstance(store, _FakeHttpBlobStore)
    assert store.token == "from-file-token"


def test_apply_factory_blobstore_env_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BLOBSTORE_URL", raising=False)
    monkeypatch.delenv("BLOBSTORE_BEARER_TOKEN_PATH", raising=False)
    monkeypatch.delenv("BLOBSTORE_BACKEND", raising=False)
    monkeypatch.setenv("FACTORY_BLOBSTORE_URL", "http://factory-blobstore:8080")
    monkeypatch.setenv("FACTORY_BLOBSTORE_TOKEN_PATH", "/run/secrets/blobstore-token")
    blobs.apply_factory_blobstore_env_aliases()
    assert os.environ["BLOBSTORE_URL"] == "http://factory-blobstore:8080"
    assert os.environ["BLOBSTORE_BEARER_TOKEN_PATH"] == "/run/secrets/blobstore-token"
    assert os.environ["BLOBSTORE_BACKEND"] == "http"


def test_rejects_pending_store_key() -> None:
    @dataclass
    class FakeRef:
        store_key: str

        def model_dump(self, *, exclude: set | None = None) -> dict:
            return {
                "store_key": self.store_key,
                "mime": "audio/wav",
                "size": 16,
                "source": "voicecli",
                "content_hash": "pending-dummy",
                "created_at": "2026-01-01T00:00:00+00:00",
                "filename": None,
                "platform_ref": None,
                "platform_message_id": None,
            }

    with pytest.raises(blobs.BlobRefValidationError, match="Pending store_key"):
        blobs.blob_ref_to_contract(FakeRef(store_key="__pending__"))


def test_accepts_valid_store_key() -> None:
    @dataclass
    class FakeRef:
        store_key: str

        def model_dump(self, *, exclude: set | None = None) -> dict:
            return {
                "store_key": self.store_key,
                "mime": "audio/wav",
                "size": 16,
                "source": "voicecli",
                "content_hash": "abc",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "filename": None,
                "platform_ref": None,
                "platform_message_id": None,
            }

    ref = FakeRef(
        store_key="sha256:deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    )
    result = blobs.blob_ref_to_contract(ref)
    assert result.store_key == ref.store_key