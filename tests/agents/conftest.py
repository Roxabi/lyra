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
)
from lyra.core.pool import Pool
from lyra.llm.base import LlmResult
from lyra.stt import STTService, TranscriptionResult

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
        platform_meta={
            "chat_id": 42,
            "topic_id": None,
            "message_id": None,
            "is_group": False,
        },
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
        platform_meta={
            "chat_id": 42,
            "topic_id": None,
            "message_id": None,
            "is_group": False,
        },
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
) -> STTService:
    """Return a MagicMock standing in for STTService with transcribe pre-configured."""
    stt = MagicMock(spec=STTService)
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
