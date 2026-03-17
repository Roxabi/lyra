"""Tests for lyra.agents.simple_agent: extract_text and SimpleAgent.process."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from lyra.agents.simple_agent import SimpleAgent
from lyra.core.agent import Agent
from lyra.core.agent_config import ModelConfig
from lyra.core.message import (
    InboundMessage,
    Response,
)
from lyra.core.pool import Pool
from lyra.core.trust import TrustLevel
from lyra.llm.base import LlmResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_inbound_message(text: str = "hello") -> InboundMessage:
    return InboundMessage(
        id="msg-1",
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


def make_pool(pool_id: str = "telegram:main:alice") -> Pool:
    return Pool(pool_id=pool_id, agent_name="lyra", ctx=MagicMock())


def make_agent(provider: object) -> SimpleAgent:
    config = Agent(
        name="lyra",
        system_prompt="You are Lyra.",
        memory_namespace="lyra",
        model_config=ModelConfig(),
    )
    return SimpleAgent(config, provider)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# TestSimpleAgentProcess
# ---------------------------------------------------------------------------


class TestSimpleAgentProcess:
    async def test_success_response(self) -> None:
        provider = MagicMock()
        provider.complete = AsyncMock(
            return_value=LlmResult(result="hello", session_id="s1")
        )
        agent = make_agent(provider)
        msg = make_inbound_message("hi")
        pool = make_pool()

        response = await agent.process(msg, pool)

        assert isinstance(response, Response)
        assert response.content == "hello"
        assert response.metadata["session_id"] == "s1"
        assert "error" not in response.metadata

    async def test_error_response(self) -> None:
        provider = MagicMock()
        provider.complete = AsyncMock(return_value=LlmResult(error="boom"))
        agent = make_agent(provider)
        msg = make_inbound_message("hi")
        pool = make_pool()

        response = await agent.process(msg, pool)

        # Internal detail must NOT leak to the user
        assert isinstance(response, Response)
        assert "boom" not in response.content
        assert response.content == "Something went wrong. Please try again."
        assert response.metadata.get("error") is True

    async def test_timeout_error_response(self) -> None:
        provider = MagicMock()
        provider.complete = AsyncMock(
            return_value=LlmResult(error="Timeout after 300s")
        )
        agent = make_agent(provider)
        msg = make_inbound_message("hi")
        pool = make_pool()

        response = await agent.process(msg, pool)

        assert isinstance(response, Response)
        assert response.content == "Your request timed out. Please try again."
        assert response.metadata.get("error") is True

    async def test_warning_response(self) -> None:
        provider = MagicMock()
        provider.complete = AsyncMock(
            return_value=LlmResult(result="ok", session_id="s1", warning="truncated")
        )
        agent = make_agent(provider)
        msg = make_inbound_message("hi")
        pool = make_pool()

        response = await agent.process(msg, pool)

        assert isinstance(response, Response)
        assert response.content == "ok"
        assert response.metadata["warning"] == "truncated"
        assert response.metadata["session_id"] == "s1"

    async def test_send_called_with_pool_id_and_text(self) -> None:
        provider = MagicMock()
        provider.complete = AsyncMock(
            return_value=LlmResult(result="ok", session_id="s1")
        )
        agent = make_agent(provider)
        msg = make_inbound_message("test text")
        pool = make_pool(pool_id="telegram:main:bob")

        await agent.process(msg, pool)

        provider.complete.assert_awaited_once()
        args = provider.complete.call_args
        assert args[0][0] == "telegram:main:bob"
        assert args[0][1] == "test text"


# ---------------------------------------------------------------------------
# T4 — on_intermediate callback forwarding based on show_intermediate flag
# ---------------------------------------------------------------------------


class TestSimpleAgentOnIntermediate:
    """SimpleAgent forwards on_intermediate based on show_intermediate flag (T4)."""

    async def _make_agent_with_show_intermediate(
        self, show_intermediate: bool, captured: dict
    ) -> "tuple[SimpleAgent, MagicMock]":
        """Return (agent, provider) capturing the on_intermediate kwarg."""
        config = Agent(
            name="lyra",
            system_prompt="You are Lyra.",
            memory_namespace="lyra",
            show_intermediate=show_intermediate,
            model_config=ModelConfig(),
        )

        async def fake_complete(
            pool_id: str,
            text: str,
            model_cfg: ModelConfig,
            system_prompt: str,
            *,
            on_intermediate=None,
            **kw,
        ) -> LlmResult:
            captured["on_intermediate"] = on_intermediate
            return LlmResult(result="done", session_id="s1")

        provider = MagicMock()
        provider.complete = fake_complete
        provider.is_alive = MagicMock(return_value=True)
        agent = SimpleAgent(config, provider)  # type: ignore[arg-type]
        return agent, provider

    async def test_show_intermediate_false_passes_none_to_provider(self) -> None:
        """show_intermediate=False → on_intermediate=None forwarded to provider."""
        # Arrange
        captured: dict = {}
        agent, _ = await self._make_agent_with_show_intermediate(False, captured)
        msg = make_inbound_message("hello")
        pool = make_pool()
        injected_cb = AsyncMock()

        # Act — pool injects an on_intermediate callback, but show_intermediate=False
        await agent.process(msg, pool, on_intermediate=injected_cb)

        # Assert — provider received None, not the injected callback
        assert captured["on_intermediate"] is None

    async def test_show_intermediate_true_passes_callback_to_provider(self) -> None:
        """show_intermediate=True → injected callback is forwarded to provider."""
        # Arrange
        captured: dict = {}
        agent, _ = await self._make_agent_with_show_intermediate(True, captured)
        msg = make_inbound_message("hello")
        pool = make_pool()
        injected_cb = AsyncMock()

        # Act — pool injects an on_intermediate callback, and show_intermediate=True
        await agent.process(msg, pool, on_intermediate=injected_cb)

        # Assert — provider received the same callback that was injected
        assert captured["on_intermediate"] is injected_cb


# ---------------------------------------------------------------------------
# TestSimpleAgentStreaming
# ---------------------------------------------------------------------------


async def _fake_async_gen(*chunks: str) -> AsyncIterator[str]:
    """Return an async iterator yielding the given chunks."""
    for chunk in chunks:
        yield chunk


def make_streaming_agent(provider: object, streaming: bool = True) -> SimpleAgent:
    """Return a SimpleAgent whose ModelConfig has streaming=streaming."""
    config = Agent(
        name="lyra",
        system_prompt="You are Lyra.",
        memory_namespace="lyra",
        model_config=ModelConfig(streaming=streaming),
    )
    return SimpleAgent(config, provider)  # type: ignore[arg-type]


class TestSimpleAgentStreaming:
    """SimpleAgent.process() returns AsyncIterator when streaming=True."""

    async def test_returns_async_iterator_when_streaming_true(self) -> None:
        # Arrange — provider has a stream() method
        provider = MagicMock()
        fake_iterator = _fake_async_gen("Hello", " world")
        provider.stream = AsyncMock(return_value=fake_iterator)
        provider.is_alive = MagicMock(return_value=True)
        agent = make_streaming_agent(provider, streaming=True)
        msg = make_inbound_message("hello")
        pool = make_pool()

        # Act
        result = await agent.process(msg, pool)

        # Assert — streaming path returns iterator, not Response
        assert result is fake_iterator

    async def test_returns_response_when_streaming_false(self) -> None:
        # Arrange — streaming=False falls through to complete()
        provider = MagicMock()
        provider.complete = AsyncMock(
            return_value=LlmResult(result="done", session_id="s1")
        )
        provider.is_alive = MagicMock(return_value=True)
        agent = make_streaming_agent(provider, streaming=False)
        msg = make_inbound_message("hello")
        pool = make_pool()

        # Act
        result = await agent.process(msg, pool)

        # Assert — non-streaming path returns a Response
        assert isinstance(result, Response)
        assert result.content == "done"

    async def test_stream_called_with_correct_args(self) -> None:
        # Arrange
        provider = MagicMock()
        fake_iterator = _fake_async_gen("token")
        provider.stream = AsyncMock(return_value=fake_iterator)
        provider.is_alive = MagicMock(return_value=True)
        agent = make_streaming_agent(provider, streaming=True)
        msg = make_inbound_message("my question")
        pool = make_pool(pool_id="tg:main:user1")

        # Act
        await agent.process(msg, pool)

        # Assert — stream() called with the right pool_id, text, model_cfg,
        # system_prompt
        provider.stream.assert_awaited_once()
        args = provider.stream.call_args[0]
        assert args[0] == "tg:main:user1"
        assert args[1] == "my question"

    async def test_returns_response_when_provider_has_no_stream_method(self) -> None:
        # Arrange — provider without stream() falls back to complete()
        provider = MagicMock(spec=["complete", "is_alive"])
        provider.complete = AsyncMock(
            return_value=LlmResult(result="fallback", session_id="s1")
        )
        provider.is_alive = MagicMock(return_value=True)
        # Make sure hasattr(provider, 'stream') is False
        assert not hasattr(provider, "stream")

        agent = make_streaming_agent(provider, streaming=True)
        msg = make_inbound_message("hello")
        pool = make_pool()

        # Act
        result = await agent.process(msg, pool)

        # Assert — complete() used as fallback
        assert isinstance(result, Response)
        assert result.content == "fallback"

    async def test_streaming_uses_pool_system_prompt_when_set(self) -> None:
        # Arrange — pool has a custom system prompt override
        provider = MagicMock()
        fake_iterator = _fake_async_gen()
        provider.stream = AsyncMock(return_value=fake_iterator)
        provider.is_alive = MagicMock(return_value=True)
        agent = make_streaming_agent(provider, streaming=True)
        msg = make_inbound_message("hello")
        pool = make_pool()
        pool._system_prompt = "Custom system prompt"

        # Act
        await agent.process(msg, pool)

        # Assert — pool system prompt passed to stream()
        args = provider.stream.call_args[0]
        assert args[3] == "Custom system prompt"

    async def test_streaming_uses_agent_system_prompt_when_pool_prompt_absent(
        self,
    ) -> None:
        # Arrange — no pool system prompt → agent config system_prompt used
        provider = MagicMock()
        fake_iterator = _fake_async_gen()
        provider.stream = AsyncMock(return_value=fake_iterator)
        provider.is_alive = MagicMock(return_value=True)
        agent = make_streaming_agent(provider, streaming=True)
        msg = make_inbound_message("hello")
        pool = make_pool()
        # pool._system_prompt is None by default

        # Act
        await agent.process(msg, pool)

        # Assert — agent.config.system_prompt used
        args = provider.stream.call_args[0]
        assert args[3] == "You are Lyra."
