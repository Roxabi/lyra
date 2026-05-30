"""Shared fixtures and helpers for tests/agents/."""

from __future__ import annotations

import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest

# Backward-compatible re-exports from agent factories
from tests.factories.agents import (  # noqa: F401
    make_audio_message,
    make_cli_pool,
    make_config,
    make_mock_stt,
    make_pool,
    make_text_message,
)

__all__ = [
    "make_audio_message",
    "make_cli_pool",
    "make_config",
    "make_mock_stt",
    "make_pool",
    "make_text_message",
]


@pytest.fixture()
def tmp_ogg_path() -> str:
    """Create a real temp file with .ogg suffix and return its path."""
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
        return f.name


def _make_mock_blob_store() -> MagicMock:
    """Build a mock BlobStorePort backed by _TEST_BLOB_REGISTRY."""
    from typing import Any

    from roxabi_contracts import BlobRef
    from tests.helpers.messages import _TEST_BLOB_REGISTRY

    async def _mock_get(store_key: str) -> bytes:
        return _TEST_BLOB_REGISTRY.get(store_key, b"mock-audio-bytes")

    async def _mock_put(data: bytes, *, mime: str, **kwargs: Any) -> BlobRef:
        ref = BlobRef(
            store_key="test-blob",
            content_hash="deadbeef",
            mime=mime,
            size=len(data),
            source="test",
        )
        _TEST_BLOB_REGISTRY[ref.store_key] = data
        return ref

    mock_store = MagicMock()
    mock_store.get = AsyncMock(side_effect=_mock_get)
    mock_store.put = AsyncMock(side_effect=_mock_put)
    return mock_store


@pytest.fixture(autouse=True)
def patch_blobstore_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Auto-use fixture: inject a mock BlobStorePort so agent tests skip the network.

    Since adapters now use self._blob_store (injected BlobStorePort), we patch
    the TelegramAdapter and DiscordAdapter __init__ to auto-wire a mock store when
    blob_store is not explicitly provided.
    """
    from lyra.adapters.discord import DiscordAdapter
    from lyra.adapters.telegram import TelegramAdapter

    mock_store = _make_mock_blob_store()

    _orig_tg_init = TelegramAdapter.__init__
    _orig_dc_init = DiscordAdapter.__init__

    def _tg_init_with_store(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if "blob_store" not in kwargs:
            kwargs["blob_store"] = mock_store
        _orig_tg_init(self, *args, **kwargs)

    def _dc_init_with_store(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if "blob_store" not in kwargs:
            kwargs["blob_store"] = mock_store
        _orig_dc_init(self, *args, **kwargs)

    monkeypatch.setattr(TelegramAdapter, "__init__", _tg_init_with_store)
    monkeypatch.setattr(DiscordAdapter, "__init__", _dc_init_with_store)
