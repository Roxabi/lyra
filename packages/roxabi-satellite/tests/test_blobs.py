"""Blobstore singleton tests (ported from voiceCLI test_blobs_factory)."""

from __future__ import annotations

from dataclasses import dataclass
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