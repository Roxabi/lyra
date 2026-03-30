"""Tests for AnthropicSdkDriver.

RED phase — these tests will fail until S2 implementation lands.
Source: src/lyra/llm/drivers/sdk.py
"""

from __future__ import annotations

import types
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

from lyra.core.agent_config import ModelConfig
from lyra.llm.drivers.sdk import (
    AnthropicSdkDriver,
)
from lyra.llm.events import ResultLlmEvent, TextLlmEvent, ToolUseLlmEvent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_driver() -> AnthropicSdkDriver:
    """Return a driver with a dummy API key (client is always mocked)."""
    return AnthropicSdkDriver(api_key="test-key")


def make_model_cfg() -> ModelConfig:
    return ModelConfig(backend="anthropic-sdk")


class _FakeStream:
    """Async context manager + async iterator for messages.stream() mocks.

    Pass ``raise_in_enter`` to simulate a connection/auth error before any
    events are delivered.
    """

    def __init__(
        self,
        events: list,
        *,
        raise_in_enter: Exception | None = None,
        cost_usd: float | None = None,
    ) -> None:
        self._events = list(events)
        self._raise_in_enter = raise_in_enter
        self._cost_usd = cost_usd
        self._idx = 0

    async def __aenter__(self) -> object:
        if self._raise_in_enter is not None:
            raise self._raise_in_enter
        return self

    async def __aexit__(self, *_args: object) -> bool:
        return False

    def __aiter__(self) -> object:
        return self

    async def __anext__(self) -> object:
        if self._idx >= len(self._events):
            raise StopAsyncIteration
        ev = self._events[self._idx]
        self._idx += 1
        return ev

    async def get_final_message(self) -> object:
        """Return mock final message with optional cost_usd on usage."""
        msg = MagicMock()
        if self._cost_usd is not None:
            msg.usage = types.SimpleNamespace(cost_usd=self._cost_usd)
        else:
            msg.usage = MagicMock(spec=[])  # no cost_usd attribute
        return msg


# ---------------------------------------------------------------------------
# Stream buffering
# ---------------------------------------------------------------------------


class TestAnthropicSdkDriverComplete:
    async def test_complete_buffers_stream(self) -> None:
        """complete() accumulates all text deltas, returns in LlmResult.result."""
        # Arrange
        driver = make_driver()
        model_cfg = make_model_cfg()

        stream_ctx = AsyncMock()
        stream_ctx.__aenter__ = AsyncMock(return_value=stream_ctx)
        stream_ctx.__aexit__ = AsyncMock(return_value=False)
        stream_ctx.text_stream = _async_iter(["hello", " world"])

        driver._client = MagicMock()
        driver._client.messages.stream = MagicMock(return_value=stream_ctx)

        # Act
        result = await driver.complete(
            pool_id="p1",
            text="hi",
            model_cfg=model_cfg,
            system_prompt="",
        )

        # Assert
        assert result.result == "hello world"

    async def test_complete_returns_ok_true(self) -> None:
        """A successful stream returns LlmResult with ok == True."""
        # Arrange
        driver = make_driver()
        model_cfg = make_model_cfg()

        stream_ctx = AsyncMock()
        stream_ctx.__aenter__ = AsyncMock(return_value=stream_ctx)
        stream_ctx.__aexit__ = AsyncMock(return_value=False)
        stream_ctx.text_stream = _async_iter(["some response"])

        driver._client = MagicMock()
        driver._client.messages.stream = MagicMock(return_value=stream_ctx)

        # Act
        result = await driver.complete(
            pool_id="p1",
            text="hi",
            model_cfg=model_cfg,
            system_prompt="",
        )

        # Assert
        assert result.ok is True
        assert result.error == ""

    async def test_complete_raises_provider_api_error_on_api_exception(self) -> None:
        """anthropic.APIError raised → ProviderApiError propagated."""
        import anthropic
        import pytest

        from lyra.errors import ProviderApiError

        # Arrange
        driver = make_driver()
        model_cfg = make_model_cfg()

        stream_ctx = AsyncMock()
        stream_ctx.__aenter__ = AsyncMock(
            side_effect=anthropic.APIError(
                message="service unavailable",
                request=MagicMock(),
                body=None,
            )
        )
        stream_ctx.__aexit__ = AsyncMock(return_value=False)

        driver._client = MagicMock()
        driver._client.messages.stream = MagicMock(return_value=stream_ctx)

        # Act + Assert
        with pytest.raises(ProviderApiError) as exc_info:
            await driver.complete(
                pool_id="p1",
                text="hi",
                model_cfg=model_cfg,
                system_prompt="",
            )
        assert exc_info.value.provider == "anthropic"
        assert exc_info.value.retryable is False
        assert exc_info.value.status_code is None

    async def test_complete_raises_provider_auth_error(self) -> None:
        """anthropic.AuthenticationError → ProviderAuthError (non-retryable)."""
        import anthropic
        import pytest

        from lyra.errors import ProviderAuthError

        driver = make_driver()
        model_cfg = make_model_cfg()

        stream_ctx = AsyncMock()
        stream_ctx.__aenter__ = AsyncMock(
            side_effect=anthropic.AuthenticationError(
                message="invalid key",
                response=MagicMock(status_code=401),
                body=None,
            )
        )
        stream_ctx.__aexit__ = AsyncMock(return_value=False)

        driver._client = MagicMock()
        driver._client.messages.stream = MagicMock(return_value=stream_ctx)

        with pytest.raises(ProviderAuthError) as exc_info:
            await driver.complete("p1", "hi", model_cfg, "")
        assert exc_info.value.retryable is False
        assert exc_info.value.status_code == 401
        assert exc_info.value.provider == "anthropic"

    async def test_complete_raises_provider_rate_limit_error(self) -> None:
        """anthropic.RateLimitError → ProviderRateLimitError (retryable)."""
        import anthropic
        import pytest

        from lyra.errors import ProviderRateLimitError

        driver = make_driver()
        model_cfg = make_model_cfg()

        stream_ctx = AsyncMock()
        stream_ctx.__aenter__ = AsyncMock(
            side_effect=anthropic.RateLimitError(
                message="rate limited",
                response=MagicMock(status_code=429),
                body=None,
            )
        )
        stream_ctx.__aexit__ = AsyncMock(return_value=False)

        driver._client = MagicMock()
        driver._client.messages.stream = MagicMock(return_value=stream_ctx)

        with pytest.raises(ProviderRateLimitError) as exc_info:
            await driver.complete("p1", "hi", model_cfg, "")
        assert exc_info.value.retryable is True
        assert exc_info.value.status_code == 429
        assert exc_info.value.provider == "anthropic"

    async def test_complete_raises_provider_error_on_unexpected_exception(self) -> None:
        """Unexpected exception → ProviderError (generic, retryable)."""
        import pytest

        from lyra.errors import ProviderError

        driver = make_driver()
        model_cfg = make_model_cfg()

        stream_ctx = AsyncMock()
        stream_ctx.__aenter__ = AsyncMock(side_effect=RuntimeError("boom"))
        stream_ctx.__aexit__ = AsyncMock(return_value=False)

        driver._client = MagicMock()
        driver._client.messages.stream = MagicMock(return_value=stream_ctx)

        with pytest.raises(ProviderError) as exc_info:
            await driver.complete("p1", "hi", model_cfg, "")
        assert exc_info.value.provider == "anthropic"
        assert exc_info.value.retryable is True


# ---------------------------------------------------------------------------
# Tool-use loop
# ---------------------------------------------------------------------------


class TestAnthropicSdkDriverToolUse:
    async def test_tool_use_turn_then_text_turn(self) -> None:
        """Two-turn loop: tool_use stop → tool result → text stop."""
        driver = make_driver()
        model_cfg = ModelConfig(
            backend="anthropic-sdk", max_turns=5, tools=("get_time",)
        )

        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.name = "get_time"
        tool_block.input = {}
        tool_block.id = "tu_1"

        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "The time is 2026-03-12T10:00:00Z"

        # Turn 1: tool_use stop
        stream1 = AsyncMock()
        stream1.__aenter__ = AsyncMock(return_value=stream1)
        stream1.__aexit__ = AsyncMock(return_value=False)
        stream1.text_stream = _async_iter(["Thinking..."])
        final1 = MagicMock()
        final1.stop_reason = "tool_use"
        final1.content = [tool_block]
        final1.usage.input_tokens = 10
        final1.usage.output_tokens = 5
        stream1.get_final_message = AsyncMock(return_value=final1)

        # Turn 2: text stop
        stream2 = AsyncMock()
        stream2.__aenter__ = AsyncMock(return_value=stream2)
        stream2.__aexit__ = AsyncMock(return_value=False)
        stream2.text_stream = _async_iter(["The time is now."])
        final2 = MagicMock()
        final2.stop_reason = "end_turn"
        final2.content = [text_block]
        final2.usage.input_tokens = 20
        final2.usage.output_tokens = 10
        stream2.get_final_message = AsyncMock(return_value=final2)

        driver._client = MagicMock()
        driver._client.messages.stream = MagicMock(side_effect=[stream1, stream2])

        result = await driver.complete("p1", "What time?", model_cfg, "")

        assert result.ok is True
        # Only final turn's text is kept (accumulated_text reset per turn)
        assert result.result == "The time is now."
        assert driver._client.messages.stream.call_count == 2

    async def test_max_turns_exhausted(self) -> None:
        """When max_turns is reached with tool_use, result includes marker."""
        driver = make_driver()
        model_cfg = ModelConfig(
            backend="anthropic-sdk", max_turns=1, tools=("get_time",)
        )

        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.name = "get_time"
        tool_block.input = {}
        tool_block.id = "tu_1"

        stream_ctx = AsyncMock()
        stream_ctx.__aenter__ = AsyncMock(return_value=stream_ctx)
        stream_ctx.__aexit__ = AsyncMock(return_value=False)
        stream_ctx.text_stream = _async_iter(["partial"])
        final = MagicMock()
        final.stop_reason = "tool_use"
        final.content = [tool_block]
        final.usage.input_tokens = 10
        final.usage.output_tokens = 5
        stream_ctx.get_final_message = AsyncMock(return_value=final)

        driver._client = MagicMock()
        driver._client.messages.stream = MagicMock(return_value=stream_ctx)

        result = await driver.complete("p1", "time?", model_cfg, "")

        assert result.ok is True
        assert "[max tool turns reached]" in result.result

    async def test_unknown_tool_returns_error_result(self) -> None:
        """Unknown tool name in tool_use block → error tool_result, loop continues."""
        driver = make_driver()
        model_cfg = ModelConfig(
            backend="anthropic-sdk", max_turns=2, tools=("get_time",)
        )

        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.name = "unknown_tool"
        tool_block.input = {}
        tool_block.id = "tu_1"

        # Turn 1: unknown tool
        stream1 = AsyncMock()
        stream1.__aenter__ = AsyncMock(return_value=stream1)
        stream1.__aexit__ = AsyncMock(return_value=False)
        stream1.text_stream = _async_iter([])
        final1 = MagicMock()
        final1.stop_reason = "tool_use"
        final1.content = [tool_block]
        final1.usage.input_tokens = 10
        final1.usage.output_tokens = 5
        stream1.get_final_message = AsyncMock(return_value=final1)

        # Turn 2: text stop after error
        stream2 = AsyncMock()
        stream2.__aenter__ = AsyncMock(return_value=stream2)
        stream2.__aexit__ = AsyncMock(return_value=False)
        stream2.text_stream = _async_iter(["I could not use that tool."])
        final2 = MagicMock()
        final2.stop_reason = "end_turn"
        final2.content = []
        final2.usage.input_tokens = 20
        final2.usage.output_tokens = 10
        stream2.get_final_message = AsyncMock(return_value=final2)

        driver._client = MagicMock()
        driver._client.messages.stream = MagicMock(side_effect=[stream1, stream2])

        result = await driver.complete("p1", "do something", model_cfg, "")

        assert result.ok is True
        assert driver._client.messages.stream.call_count == 2


class TestExecuteTool:
    async def test_get_time_returns_iso_string(self) -> None:
        driver = make_driver()
        result = await driver._execute_tool("get_time", {})
        assert "T" in result  # ISO 8601

    async def test_unknown_tool_raises_value_error(self) -> None:
        import pytest

        driver = make_driver()
        with pytest.raises(ValueError, match="Unknown tool"):
            await driver._execute_tool("nonexistent", {})


# ---------------------------------------------------------------------------
# stream() — LlmEvent async iteration
# ---------------------------------------------------------------------------


class TestAnthropicSdkDriverStream:
    """AnthropicSdkDriver.stream() yields typed LlmEvent objects."""

    def _text_ev(self, text: str) -> object:
        return types.SimpleNamespace(
            type="content_block_delta",
            delta=types.SimpleNamespace(type="text_delta", text=text),
        )

    def _tool_start_ev(self, name: str, id_: str) -> object:
        return types.SimpleNamespace(
            type="content_block_start",
            content_block=types.SimpleNamespace(type="tool_use", name=name, id=id_),
        )

    async def _collect(self, driver: AnthropicSdkDriver, raw_events: list) -> list:
        stream = _FakeStream(raw_events)
        driver._client.messages.stream = MagicMock(return_value=stream)
        return [
            e async for e in await driver.stream("pool-1", "hi", make_model_cfg(), "")
        ]

    async def test_text_delta_yields_text_llm_event(self) -> None:
        # Arrange — one text_delta event
        driver = make_driver()

        # Act
        result = await self._collect(driver, [self._text_ev("hello")])

        # Assert — TextLlmEvent then terminal ResultLlmEvent
        assert result[0] == TextLlmEvent(text="hello")
        assert isinstance(result[1], ResultLlmEvent)
        assert result[1].is_error is False

    async def test_content_block_start_tool_use_yields_tool_use_event(
        self,
    ) -> None:
        # Arrange — content_block_start with type==tool_use
        driver = make_driver()

        # Act
        result = await self._collect(driver, [self._tool_start_ev("Bash", "tu_001")])

        # Assert — ToolUseLlmEvent emitted before terminal ResultLlmEvent
        assert result[0] == ToolUseLlmEvent(
            tool_name="Bash", tool_id="tu_001", input={}
        )
        assert isinstance(result[1], ResultLlmEvent)
        assert result[1].is_error is False

    async def test_unknown_event_type_is_skipped(self) -> None:
        # Arrange — unrecognised event type → silently dropped
        driver = make_driver()
        other = types.SimpleNamespace(type="message_start")

        # Act
        result = await self._collect(driver, [other])

        # Assert — only terminal ResultLlmEvent emitted
        assert len(result) == 1
        assert isinstance(result[0], ResultLlmEvent)
        assert result[0].is_error is False
        assert result[0].cost_usd is None

    async def test_exception_yields_error_result_not_reraise(self) -> None:
        # Arrange — stream context manager raises immediately
        driver = make_driver()
        stream = _FakeStream([], raise_in_enter=RuntimeError("connection failed"))
        driver._client.messages.stream = MagicMock(return_value=stream)

        # Act — must NOT raise (is_error sentinel terminates cleanly)
        result = [
            e async for e in await driver.stream("pool-1", "hi", make_model_cfg(), "")
        ]

        # Assert — single ResultLlmEvent with is_error=True
        assert len(result) == 1
        assert isinstance(result[0], ResultLlmEvent)
        assert result[0].is_error is True
        assert result[0].cost_usd is None


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


class TestAnthropicSdkDriverCapabilities:
    def test_capabilities_streaming_false(self) -> None:
        """Driver declares streaming == False (buffers internally)."""
        driver = make_driver()
        assert driver.capabilities["streaming"] is False

    def test_capabilities_auth_api_key(self) -> None:
        """Driver declares auth == 'api_key'."""
        driver = make_driver()
        assert driver.capabilities["auth"] == "api_key"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _async_iter(items: list[str]) -> AsyncIterator[str]:
    """Yield items as an async iterator (simulates text_stream)."""
    for item in items:
        yield item
