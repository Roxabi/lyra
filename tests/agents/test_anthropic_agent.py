"""Tests for AnthropicAgent: construction, process, sdk_history,
tool loop, system prompt.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.agent import Agent, ModelConfig
from lyra.core.auth import TrustLevel
from lyra.core.message import InboundMessage
from lyra.core.pool import Pool
from lyra.llm.base import LlmResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_system_prompt(call_args: object) -> str:
    """Extract system_prompt from provider.complete() call args."""
    if len(call_args.args) > 3:  # type: ignore[union-attr]
        return call_args.args[3]  # type: ignore[union-attr]
    return call_args.kwargs.get("system_prompt", "")  # type: ignore[union-attr]


def make_message(text: str = "hello") -> InboundMessage:
    return InboundMessage(
        id="msg-1",
        platform="telegram",
        bot_id="main",
        scope_id="chat:42",
        user_id="tg:user:alice",
        user_name="Alice",
        is_mention=False,
        text=text,
        text_raw=text,
        timestamp=datetime.now(timezone.utc),
        platform_meta={
            "chat_id": 42,
            "topic_id": None,
            "is_group": False,
            "message_id": None,
        },
        trust_level=TrustLevel.TRUSTED,
    )


def make_pool(pool_id: str = "telegram:main:alice") -> Pool:
    return Pool(pool_id=pool_id, agent_name="lyra", ctx=MagicMock())


def make_config(
    backend: str = "anthropic-sdk",
    system_prompt: str = "",
    tools: tuple[str, ...] = (),
    max_turns: int = 10,
) -> Agent:
    return Agent(
        name="lyra",
        system_prompt=system_prompt,
        memory_namespace="lyra",
        model_config=ModelConfig(
            backend=backend,
            model="claude-sonnet-4-5",
            max_turns=max_turns,
            tools=tools,
        ),
    )


def make_mock_provider(reply: str = "hello world") -> MagicMock:
    """Return a mock LlmProvider that returns a successful LlmResult."""
    provider = MagicMock()
    provider.capabilities = {"streaming": False, "auth": "api_key"}
    provider.complete = AsyncMock(return_value=LlmResult(result=reply))
    return provider


# ---------------------------------------------------------------------------
# TestConstruction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_provider_stored_on_agent(self) -> None:
        from lyra.agents.anthropic_agent import AnthropicAgent

        provider = make_mock_provider()
        agent = AnthropicAgent(make_config(), provider)
        assert agent._provider is provider

    def test_name_from_config(self) -> None:
        from lyra.agents.anthropic_agent import AnthropicAgent

        agent = AnthropicAgent(make_config(), make_mock_provider())
        assert agent.name == "lyra"


# ---------------------------------------------------------------------------
# TestProcess — non-streaming Response
# ---------------------------------------------------------------------------


class TestProcess:
    async def test_process_returns_response_with_text(self) -> None:
        from lyra.agents.anthropic_agent import AnthropicAgent
        from lyra.core.message import Response

        provider = make_mock_provider("Hello world!")
        agent = AnthropicAgent(make_config(), provider)

        msg = make_message("hi")
        pool = make_pool()
        response = await agent.process(msg, pool)

        assert isinstance(response, Response)
        assert response.content == "Hello world!"

    async def test_process_appends_to_sdk_history(self) -> None:
        from lyra.agents.anthropic_agent import AnthropicAgent

        provider = make_mock_provider("response text")
        agent = AnthropicAgent(make_config(), provider)

        msg = make_message("hi")
        pool = make_pool()
        await agent.process(msg, pool)

        assert len(pool.sdk_history) == 2
        assert pool.sdk_history[0] == {"role": "user", "content": "hi"}
        assert pool.sdk_history[1] == {
            "role": "assistant",
            "content": "response text",
        }

    async def test_sdk_history_eviction(self) -> None:
        from lyra.agents.anthropic_agent import AnthropicAgent

        config = make_config()
        provider = MagicMock()
        provider.capabilities = {"streaming": False, "auth": "api_key"}
        pool = make_pool()
        pool.max_sdk_history = 4  # 4 messages max (2 simple exchanges)

        agent = AnthropicAgent(config, provider)

        # Fill with 3 exchanges (6 messages, should trim to 4)
        for i in range(3):
            provider.complete = AsyncMock(return_value=LlmResult(result=f"reply {i}"))
            await agent.process(make_message(f"msg {i}"), pool)

        assert len(pool.sdk_history) == 4  # 2 exchanges
        assert pool.sdk_history[0]["content"] == "msg 1"  # oldest kept

    async def test_provider_receives_history_in_messages(self) -> None:
        from lyra.agents.anthropic_agent import AnthropicAgent

        provider = make_mock_provider("second response")
        agent = AnthropicAgent(make_config(), provider)

        pool = make_pool()
        # Pre-seed history
        pool.extend_sdk_history(
            [
                {"role": "user", "content": "first question"},
                {"role": "assistant", "content": "first answer"},
            ]
        )

        msg = make_message("second question")
        await agent.process(msg, pool)

        call_kwargs = provider.complete.call_args
        messages = call_kwargs.kwargs["messages"]
        assert messages[0] == {"role": "user", "content": "first question"}
        assert messages[1] == {"role": "assistant", "content": "first answer"}
        assert messages[2] == {"role": "user", "content": "second question"}

    async def test_provider_error_returns_error_response(self) -> None:
        from lyra.agents.anthropic_agent import AnthropicAgent

        provider = MagicMock()
        provider.capabilities = {"streaming": False, "auth": "api_key"}
        provider.complete = AsyncMock(
            return_value=LlmResult(error="service unavailable")
        )
        agent = AnthropicAgent(make_config(), provider)

        msg = make_message("hi")
        pool = make_pool()
        response = await agent.process(msg, pool)

        assert response.metadata.get("error") is True

    async def test_provider_exception_propagates(self) -> None:
        from lyra.agents.anthropic_agent import AnthropicAgent

        provider = MagicMock()
        provider.capabilities = {"streaming": False, "auth": "api_key"}
        provider.complete = AsyncMock(side_effect=RuntimeError("network error"))
        agent = AnthropicAgent(make_config(), provider)

        msg = make_message("hi")
        pool = make_pool()

        with pytest.raises(RuntimeError, match="network error"):
            await agent.process(msg, pool)


# ---------------------------------------------------------------------------
# TestSystemPrompt
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    async def test_system_prompt_passed_to_provider(self) -> None:
        from lyra.agents.anthropic_agent import AnthropicAgent

        config = make_config(system_prompt="You are Lyra, a helpful assistant.")
        provider = make_mock_provider("Hello!")
        agent = AnthropicAgent(config, provider)

        msg = make_message("hi")
        pool = make_pool()
        await agent.process(msg, pool)

        call_args = provider.complete.call_args
        system_prompt_sent = _extract_system_prompt(call_args)
        assert system_prompt_sent == "You are Lyra, a helpful assistant."

    async def test_empty_system_prompt_passed_as_empty_string(self) -> None:
        from lyra.agents.anthropic_agent import AnthropicAgent

        config = make_config(system_prompt="")
        provider = make_mock_provider("Hello!")
        agent = AnthropicAgent(config, provider)

        msg = make_message("hi")
        pool = make_pool()
        await agent.process(msg, pool)

        call_args = provider.complete.call_args
        system_prompt_sent = _extract_system_prompt(call_args)
        assert system_prompt_sent == ""


# ---------------------------------------------------------------------------
# TestOverlayAppliedInProcess — runtime_config overlay (issue #135)
# ---------------------------------------------------------------------------


class TestOverlayAppliedInProcess:
    """RuntimeConfig overlay is applied to the provider call in process()."""

    async def test_overlay_model_and_system_applied(self) -> None:
        """process() uses overlaid model and system including style + language."""
        # Arrange
        from lyra.agents.anthropic_agent import AnthropicAgent
        from lyra.core.agent import ModelConfig
        from lyra.core.runtime_config import RuntimeConfig

        config = make_config(system_prompt="Base prompt.")
        runtime_config = RuntimeConfig(style="detailed", language="fr", temperature=0.3)
        provider = make_mock_provider("Bonjour!")
        agent = AnthropicAgent(config, provider, runtime_config=runtime_config)

        msg = make_message("hello")
        pool = make_pool()

        # Act
        await agent.process(msg, pool)

        # Assert — provider called with overlaid ModelConfig
        call_kwargs = provider.complete.call_args
        model_cfg = call_kwargs.args[2]
        assert isinstance(model_cfg, ModelConfig)

        # Assert — system prompt includes the detailed style instruction
        system = call_kwargs.args[3]
        assert "detailed" in system.lower() or "thorough" in system.lower()

        # Assert — system prompt includes the language instruction
        assert "Reply in fr." in system


# ---------------------------------------------------------------------------
# TestSessionCommandWiring — issue #99
# ---------------------------------------------------------------------------


class TestSessionCommandWiring:
    """Router is built with session_driver and /add /explain /summarize registered."""

    def test_router_session_driver_set_to_provider(self) -> None:
        from lyra.agents.anthropic_agent import AnthropicAgent

        provider = make_mock_provider()
        agent = AnthropicAgent(make_config(), provider)
        assert agent.command_router._session_driver is provider

    def test_session_handlers_contains_add_explain_summarize(self) -> None:
        from lyra.agents.anthropic_agent import AnthropicAgent

        provider = make_mock_provider()
        agent = AnthropicAgent(make_config(), provider)
        session_keys = set(agent.command_router._session_handlers.keys())
        assert "/add" in session_keys
        assert "/explain" in session_keys
        assert "/summarize" in session_keys

    def test_session_handlers_have_correct_descriptions(self) -> None:
        from lyra.agents.anthropic_agent import AnthropicAgent

        provider = make_mock_provider()
        agent = AnthropicAgent(make_config(), provider)
        handlers = agent.command_router._session_handlers
        assert "vault" in handlers["/add"].description.lower()
        assert "explain" in handlers["/explain"].description.lower()
        assert "summarize" in handlers["/summarize"].description.lower()
