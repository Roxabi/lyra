"""Tests for AnthropicSdkDriver.

RED phase — these tests will fail until S2 implementation lands.
Source: src/lyra/llm/drivers/sdk.py
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from lyra.llm.drivers.sdk import (  # type: ignore[reportMissingImports]
    AnthropicSdkDriver,
)

from lyra.core.agent import ModelConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_driver() -> AnthropicSdkDriver:
    """Return a driver with a dummy API key (client is always mocked)."""
    return AnthropicSdkDriver(api_key="test-key")


def make_model_cfg() -> ModelConfig:
    return ModelConfig(backend="anthropic-sdk")


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

    async def test_complete_error_on_api_exception(self) -> None:
        """anthropic.APIError raised → LlmResult.ok == False, error set."""
        import anthropic

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

        # Act
        result = await driver.complete(
            pool_id="p1",
            text="hi",
            model_cfg=model_cfg,
            system_prompt="",
        )

        # Assert
        assert result.ok is False
        assert result.error != ""


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


async def _async_iter(items: list[str]):  # type: ignore[return]
    """Yield items as an async iterator (simulates text_stream)."""
    for item in items:
        yield item
