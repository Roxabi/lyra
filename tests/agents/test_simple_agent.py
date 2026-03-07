"""Tests for lyra.agents.simple_agent: extract_text and SimpleAgent.process."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from lyra.agents.simple_agent import SimpleAgent
from lyra.core.agent import Agent, ModelConfig
from lyra.core.cli_pool import CliResult
from lyra.core.message import (
    AudioContent,
    ImageContent,
    Message,
    MessageType,
    Platform,
    Response,
    TelegramContext,
    TextContent,
    extract_text,
)
from lyra.core.pool import Pool

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_message(content: object = "hello") -> Message:
    return Message(
        id="msg-1",
        platform=Platform.TELEGRAM,
        bot_id="main",
        user_id="alice",
        user_name="Alice",
        is_mention=False,
        is_from_bot=False,
        content=content,  # type: ignore[arg-type]
        type=MessageType.TEXT,
        timestamp=datetime.now(timezone.utc),
        platform_context=TelegramContext(chat_id=42),
    )


def make_pool(pool_id: str = "telegram:main:alice") -> Pool:
    return Pool(pool_id=pool_id, agent_name="lyra")


def make_agent(cli_pool: object) -> SimpleAgent:
    config = Agent(
        name="lyra",
        system_prompt="You are Lyra.",
        memory_namespace="lyra",
        model_config=ModelConfig(),
    )
    return SimpleAgent(config, cli_pool)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# TestExtractText
# ---------------------------------------------------------------------------


class TestExtractText:
    def test_plain_string(self) -> None:
        msg = make_message(content="plain text")
        assert extract_text(msg) == "plain text"

    def test_text_content(self) -> None:
        msg = make_message(content=TextContent(text="hello"))
        assert extract_text(msg) == "hello"

    def test_image_content_url_fallback(self) -> None:
        url = "https://example.com/img.png"
        msg = make_message(content=ImageContent(url=url))
        assert extract_text(msg) == f"[image: {url}]"

    def test_audio_content_url_fallback(self) -> None:
        url = "https://example.com/audio.ogg"
        msg = make_message(content=AudioContent(url=url))
        assert extract_text(msg) == f"[audio: {url}]"


# ---------------------------------------------------------------------------
# TestSimpleAgentProcess
# ---------------------------------------------------------------------------


class TestSimpleAgentProcess:
    async def test_success_response(self) -> None:
        cli_pool = MagicMock()
        cli_pool.send = AsyncMock(
            return_value=CliResult(result="hello", session_id="s1")
        )
        agent = make_agent(cli_pool)
        msg = make_message("hi")
        pool = make_pool()

        response = await agent.process(msg, pool)

        assert isinstance(response, Response)
        assert response.content == "hello"
        assert response.metadata["session_id"] == "s1"
        assert "error" not in response.metadata

    async def test_error_response(self) -> None:
        cli_pool = MagicMock()
        cli_pool.send = AsyncMock(return_value=CliResult(error="boom"))
        agent = make_agent(cli_pool)
        msg = make_message("hi")
        pool = make_pool()

        response = await agent.process(msg, pool)

        # Internal detail must NOT leak to the user
        assert "boom" not in response.content
        assert response.content == "Something went wrong. Please try again."
        assert response.metadata.get("error") is True

    async def test_timeout_error_response(self) -> None:
        cli_pool = MagicMock()
        cli_pool.send = AsyncMock(return_value=CliResult(error="Timeout after 300s"))
        agent = make_agent(cli_pool)
        msg = make_message("hi")
        pool = make_pool()

        response = await agent.process(msg, pool)

        assert response.content == "Response timed out. Please try again."
        assert response.metadata.get("error") is True

    async def test_warning_response(self) -> None:
        cli_pool = MagicMock()
        cli_pool.send = AsyncMock(
            return_value=CliResult(result="ok", session_id="s1", warning="truncated")
        )
        agent = make_agent(cli_pool)
        msg = make_message("hi")
        pool = make_pool()

        response = await agent.process(msg, pool)

        assert response.content == "ok"
        assert response.metadata["warning"] == "truncated"
        assert response.metadata["session_id"] == "s1"

    async def test_send_called_with_pool_id_and_text(self) -> None:
        cli_pool = MagicMock()
        cli_pool.send = AsyncMock(return_value=CliResult(result="ok", session_id="s1"))
        agent = make_agent(cli_pool)
        msg = make_message("test text")
        pool = make_pool(pool_id="telegram:main:bob")

        await agent.process(msg, pool)

        cli_pool.send.assert_awaited_once()
        args = cli_pool.send.call_args
        assert args[0][0] == "telegram:main:bob"
        assert args[0][1] == "test text"
