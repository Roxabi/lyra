"""Tests for AnthropicAgent: construction, process, sdk_history,
tool loop, system prompt.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.agent import Agent, ModelConfig
from lyra.core.auth import TrustLevel
from lyra.core.message import Message, MessageType, Platform, TelegramContext
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
        trust_level=TrustLevel.TRUSTED,
        platform_context=TelegramContext(chat_id=42),
    )


def make_pool(pool_id: str = "telegram:main:alice") -> Pool:
    return Pool(pool_id=pool_id, agent_name="lyra", hub=MagicMock())


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


def _make_mock_stream(text_chunks: list[str], stop_reason: str = "end_turn"):
    """Create a mock stream context manager that yields text_chunks."""
    mock_final = MagicMock()
    mock_final.content = [MagicMock(type="text", text="".join(text_chunks))]
    mock_final.stop_reason = stop_reason
    mock_final.usage.input_tokens = 100
    mock_final.usage.output_tokens = 50

    async def text_stream_iter():
        for chunk in text_chunks:
            yield chunk

    stream_ctx = MagicMock()
    stream_ctx.text_stream = text_stream_iter()
    stream_ctx.get_final_message = AsyncMock(return_value=mock_final)

    ctx_manager = AsyncMock()
    ctx_manager.__aenter__ = AsyncMock(return_value=stream_ctx)
    ctx_manager.__aexit__ = AsyncMock(return_value=False)

    return ctx_manager, mock_final


def _make_tool_use_stream(
    text_chunks: list[str],
    tool_blocks: list[dict],
):
    """Create a mock stream that returns tool_use stop_reason."""
    mock_final = MagicMock()
    content_blocks = []
    for chunk in text_chunks:
        block = MagicMock(type="text", text=chunk)
        content_blocks.append(block)
    for tb in tool_blocks:
        block = MagicMock(type="tool_use", name=tb["name"], input=tb.get("input", {}))
        block.id = tb.get("id", "tool-1")
        content_blocks.append(block)

    mock_final.content = content_blocks
    mock_final.stop_reason = "tool_use"
    mock_final.usage.input_tokens = 100
    mock_final.usage.output_tokens = 50

    async def text_stream_iter():
        for chunk in text_chunks:
            yield chunk

    stream_ctx = MagicMock()
    stream_ctx.text_stream = text_stream_iter()
    stream_ctx.get_final_message = AsyncMock(return_value=mock_final)

    ctx_manager = AsyncMock()
    ctx_manager.__aenter__ = AsyncMock(return_value=stream_ctx)
    ctx_manager.__aexit__ = AsyncMock(return_value=False)

    return ctx_manager, mock_final


# ---------------------------------------------------------------------------
# TestConstruction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_missing_api_key_raises_system_exit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        from lyra.agents.anthropic_agent import AnthropicAgent

        with pytest.raises(SystemExit, match="ANTHROPIC_API_KEY"):
            AnthropicAgent(make_config())

    def test_valid_api_key_creates_agent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
        from lyra.agents.anthropic_agent import AnthropicAgent

        agent = AnthropicAgent(make_config())
        assert agent.name == "lyra"
        assert agent._client is not None


# ---------------------------------------------------------------------------
# TestProcess — streaming
# ---------------------------------------------------------------------------


class TestProcess:
    async def test_process_yields_text_deltas(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
        from lyra.agents.anthropic_agent import AnthropicAgent

        agent = AnthropicAgent(make_config())
        stream_ctx, _ = _make_mock_stream(["Hello", " world", "!"])
        agent._client.messages.stream = MagicMock(return_value=stream_ctx)

        msg = make_message("hi")
        pool = make_pool()
        chunks = []
        async for delta in agent.process(msg, pool):
            chunks.append(delta)

        assert chunks == ["Hello", " world", "!"]

    async def test_process_appends_to_sdk_history(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
        from lyra.agents.anthropic_agent import AnthropicAgent

        agent = AnthropicAgent(make_config())
        stream_ctx, _ = _make_mock_stream(["response text"])
        agent._client.messages.stream = MagicMock(return_value=stream_ctx)

        msg = make_message("hi")
        pool = make_pool()
        async for _ in agent.process(msg, pool):
            pass

        assert len(pool.sdk_history) == 2
        assert pool.sdk_history[0] == {"role": "user", "content": "hi"}
        assert pool.sdk_history[1] == {
            "role": "assistant",
            "content": "response text",
        }

    async def test_sdk_history_eviction(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
        from lyra.agents.anthropic_agent import AnthropicAgent

        config = make_config()
        agent = AnthropicAgent(config)
        pool = make_pool()
        pool.max_sdk_history = 4  # 4 messages max (2 simple exchanges)

        # Fill with 3 exchanges (6 messages, should trim to 4)
        for i in range(3):
            stream_ctx, _ = _make_mock_stream([f"reply {i}"])
            agent._client.messages.stream = MagicMock(return_value=stream_ctx)
            async for _ in agent.process(make_message(f"msg {i}"), pool):
                pass

        assert len(pool.sdk_history) == 4  # 2 exchanges
        assert pool.sdk_history[0]["content"] == "msg 1"  # oldest kept


# ---------------------------------------------------------------------------
# TestToolLoop
# ---------------------------------------------------------------------------


class TestToolLoop:
    async def test_tool_use_calls_execute_tool(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
        from lyra.agents.anthropic_agent import AnthropicAgent

        config = make_config(tools=("get_time",))
        agent = AnthropicAgent(config)

        # First call returns tool_use, second returns text
        tool_stream, _ = _make_tool_use_stream(
            ["Let me check..."],
            [{"name": "get_time", "id": "tool-1"}],
        )
        final_stream, _ = _make_mock_stream(["The time is now."])

        agent._client.messages.stream = MagicMock(
            side_effect=[tool_stream, final_stream]
        )

        msg = make_message("What time is it?")
        pool = make_pool()
        chunks = []
        async for delta in agent.process(msg, pool):
            chunks.append(delta)

        assert "Let me check..." in chunks
        assert "The time is now." in chunks

    async def test_tool_error_returns_is_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
        from lyra.agents.anthropic_agent import AnthropicAgent

        config = make_config(tools=("get_time",))
        agent = AnthropicAgent(config)

        # Tool use with unknown tool
        tool_stream, _ = _make_tool_use_stream(
            [""],
            [{"name": "unknown_tool", "id": "tool-2"}],
        )
        final_stream, _ = _make_mock_stream(["I couldn't do that."])

        call_count = 0

        def track_stream_calls(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return tool_stream
            return final_stream

        agent._client.messages.stream = MagicMock(side_effect=track_stream_calls)

        msg = make_message("Do something impossible")
        pool = make_pool()
        chunks = []
        async for delta in agent.process(msg, pool):
            chunks.append(delta)

        # Verify the tool error was passed to the API
        second_call_kwargs = agent._client.messages.stream.call_args_list[1]
        messages = second_call_kwargs.kwargs.get(
            "messages", second_call_kwargs[1].get("messages", [])
        )
        # Find the tool_result message
        tool_result_msg = None
        for m in messages:
            if m.get("role") == "user" and isinstance(m.get("content"), list):
                for item in m["content"]:
                    if isinstance(item, dict) and item.get("type") == "tool_result":
                        tool_result_msg = item
                        break
        assert tool_result_msg is not None
        assert tool_result_msg["is_error"] is True

    async def test_max_turns_terminates_loop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
        from lyra.agents.anthropic_agent import AnthropicAgent

        config = make_config(tools=("get_time",), max_turns=2)
        agent = AnthropicAgent(config)

        # Both calls return tool_use - loop should stop at max_turns
        def make_tool_stream():
            return _make_tool_use_stream(
                ["thinking..."],
                [{"name": "get_time", "id": "tool-1"}],
            )[0]

        agent._client.messages.stream = MagicMock(
            side_effect=[make_tool_stream(), make_tool_stream()]
        )

        msg = make_message("loop forever")
        pool = make_pool()
        chunks = []
        async for delta in agent.process(msg, pool):
            chunks.append(delta)

        assert agent._client.messages.stream.call_count == 2
        assert " [max tool turns reached]" in chunks


# ---------------------------------------------------------------------------
# TestSystemPrompt
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    async def test_system_prompt_passed_to_sdk(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
        from lyra.agents.anthropic_agent import AnthropicAgent

        config = make_config(system_prompt="You are Lyra, a helpful assistant.")
        agent = AnthropicAgent(config)

        stream_ctx, _ = _make_mock_stream(["Hello!"])
        agent._client.messages.stream = MagicMock(return_value=stream_ctx)

        msg = make_message("hi")
        pool = make_pool()
        async for _ in agent.process(msg, pool):
            pass

        call_kwargs = agent._client.messages.stream.call_args
        assert call_kwargs.kwargs.get("system") == "You are Lyra, a helpful assistant."

    async def test_empty_system_prompt_omitted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
        from lyra.agents.anthropic_agent import AnthropicAgent

        config = make_config(system_prompt="")
        agent = AnthropicAgent(config)

        stream_ctx, _ = _make_mock_stream(["Hello!"])
        agent._client.messages.stream = MagicMock(return_value=stream_ctx)

        msg = make_message("hi")
        pool = make_pool()
        async for _ in agent.process(msg, pool):
            pass

        call_kwargs = agent._client.messages.stream.call_args
        assert "system" not in call_kwargs.kwargs


# ---------------------------------------------------------------------------
# TestOverlayAppliedInProcess — runtime_config overlay (issue #135)
# ---------------------------------------------------------------------------


class TestOverlayAppliedInProcess:
    """RuntimeConfig overlay is applied to the Anthropic SDK call in process()."""

    async def test_overlay_temperature_and_system_applied(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """process() uses temperature=0.3 and system includes style + language."""
        # Arrange
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
        from lyra.agents.anthropic_agent import AnthropicAgent
        from lyra.core.runtime_config import RuntimeConfig

        config = make_config(system_prompt="Base prompt.")
        runtime_config = RuntimeConfig(style="detailed", language="fr", temperature=0.3)
        agent = AnthropicAgent(config, runtime_config=runtime_config)

        stream_ctx, _ = _make_mock_stream(["Bonjour!"])
        agent._client.messages.stream = MagicMock(return_value=stream_ctx)

        msg = make_message("hello")
        pool = make_pool()

        # Act — drain the async generator
        chunks = []
        async for delta in agent.process(msg, pool):
            chunks.append(delta)

        # Assert — SDK called with the overlaid temperature
        call_kwargs = agent._client.messages.stream.call_args.kwargs
        assert call_kwargs["temperature"] == 0.3

        # Assert — system prompt includes the detailed style instruction
        system = call_kwargs.get("system", "")
        assert "detailed" in system.lower() or "thorough" in system.lower()

        # Assert — system prompt includes the language instruction
        assert "Reply in fr." in system
