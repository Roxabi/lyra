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


@pytest.fixture(autouse=True)
def patch_blobstore_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Auto-use fixture: mock HttpBlobStore so agent tests don't hit the network."""
    from typing import Any

    from roxabi_contracts import BlobRef
    from tests.helpers.messages import _TEST_BLOB_REGISTRY

    async def _mock_get(store_key: str) -> bytes:
        return _TEST_BLOB_REGISTRY.get(store_key, b"mock-audio-bytes")

    def _mock_put(data: bytes, *, mime: str, **kwargs: Any) -> BlobRef:
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
    for target in (
        "lyra.adapters.shared._blobstore_client.get_blobstore_client",
        "lyra.adapters.telegram.telegram_audio.get_blobstore_client",
        "lyra.adapters.discord.discord_audio_outbound.get_blobstore_client",
        "lyra.adapters.shared._shared_audio.get_blobstore_client",
    ):
        monkeypatch.setattr(target, lambda: mock_store)
