"""Tests for lyra.agents.simple_agent: extract_text and SimpleAgent.process."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from lyra.agents.simple_agent import SimpleAgent
from lyra.core.agent import Agent, ModelConfig
from lyra.core.auth import TrustLevel
from lyra.core.message import (
    InboundMessage,
    Response,
)
from lyra.core.pool import Pool
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
