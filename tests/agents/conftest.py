"""Shared fixtures and helpers for tests/agents/."""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.agent import Agent
from lyra.core.agent.agent_config import ModelConfig
from lyra.core.auth.trust import TrustLevel
from lyra.core.messaging.message import (
    Attachment,
    InboundMessage,
    TelegramMeta,
)
from lyra.core.pool import Pool
from lyra.core.ports.stt import STTProtocol, TranscriptionResult
from lyra.llm.base import LlmResult

# ---------------------------------------------------------------------------
# Message factories
# ---------------------------------------------------------------------------


def make_audio_message(url: str) -> InboundMessage:
    return InboundMessage(
        id="msg-audio",
        platform="telegram",
        bot_id="main",
        scope_id="chat:42",
        user_id="alice",
        user_name="Alice",
        is_mention=False,
        text="",
        text_raw="",
        attachments=[
            Attachment(type="audio", url_or_path_or_bytes=url, mime_type="audio/ogg"),
        ],
        timestamp=datetime.now(timezone.utc),
        platform_meta=TelegramMeta(chat_id=42),
        trust_level=TrustLevel.TRUSTED,
    )


def make_text_message(text: str = "hello") -> InboundMessage:
    return InboundMessage(
        id="msg-text",
        platform="telegram",
        bot_id="main",
        scope_id="chat:42",
        user_id="alice",
        user_name="Alice",
        is_mention=False,
        text=text,
        text_raw=text,
        timestamp=datetime.now(timezone.utc),
        platform_meta=TelegramMeta(chat_id=42),
        trust_level=TrustLevel.TRUSTED,
    )


# ---------------------------------------------------------------------------
# Object factories
# ---------------------------------------------------------------------------


def make_pool(pool_id: str = "telegram:main:alice") -> Pool:
    return Pool(pool_id=pool_id, agent_name="lyra", ctx=MagicMock())


def make_config() -> Agent:
    return Agent(
        name="lyra",
        system_prompt="You are Lyra.",
        memory_namespace="lyra",
        llm_config=ModelConfig(),
    )


def make_mock_stt(
    result: TranscriptionResult | None = None, raises: Exception | None = None
) -> STTProtocol:
    """Return a MagicMock standing in for STTProtocol with transcribe pre-configured."""
    stt = MagicMock(spec=STTProtocol)
    if raises is not None:
        stt.transcribe = AsyncMock(side_effect=raises)
    else:
        stt.transcribe = AsyncMock(return_value=result)
    return stt


def make_cli_pool(result: str = "cli response") -> MagicMock:
    provider = MagicMock()
    provider.complete = AsyncMock(
        return_value=LlmResult(result=result, session_id="s1")
    )
    return provider


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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
