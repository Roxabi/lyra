"""Shared fixtures and helpers for tests/agents/."""

from __future__ import annotations

import tempfile

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
from tests.factories.blobs import _make_mock_blob_store

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
