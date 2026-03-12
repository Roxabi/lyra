"""Tests for ClaudeCliDriver.

RED phase — these tests will fail until S3 implementation lands.
Source: src/lyra/llm/drivers/cli.py
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from lyra.core.agent import ModelConfig
from lyra.core.cli_pool import CliResult
from lyra.llm.base import LlmResult
from lyra.llm.drivers.cli import ClaudeCliDriver  # type: ignore[reportMissingImports]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_pool(send_return: CliResult) -> MagicMock:
    pool = MagicMock()
    pool.send = AsyncMock(return_value=send_return)
    return pool


def make_driver(send_return: CliResult) -> tuple[ClaudeCliDriver, MagicMock]:
    pool = make_pool(send_return)
    return ClaudeCliDriver(pool=pool), pool


def make_model_cfg() -> ModelConfig:
    return ModelConfig(backend="claude-cli")


# ---------------------------------------------------------------------------
# Delegation
# ---------------------------------------------------------------------------


class TestClaudeCliDriverComplete:
    async def test_complete_delegates_to_pool(self) -> None:
        """complete() calls pool.send() with the right args."""
        # Arrange
        driver, pool = make_driver(CliResult(result="ok", session_id="s1"))
        model_cfg = make_model_cfg()

        # Act
        await driver.complete(
            pool_id="my-pool",
            text="hello",
            model_cfg=model_cfg,
            system_prompt="be helpful",
        )

        # Assert
        pool.send.assert_awaited_once_with("my-pool", "hello", model_cfg, "be helpful")

    async def test_complete_translates_success(self) -> None:
        """CliResult(result='hi', session_id='s1') → LlmResult(result='hi', ok=True)."""
        # Arrange
        driver, _ = make_driver(CliResult(result="hi", session_id="s1"))

        # Act
        result = await driver.complete(
            pool_id="p1",
            text="hi",
            model_cfg=make_model_cfg(),
            system_prompt="",
        )

        # Assert
        assert isinstance(result, LlmResult)
        assert result.result == "hi"
        assert result.session_id == "s1"
        assert result.ok is True
        assert result.error == ""

    async def test_complete_translates_error(self) -> None:
        """CliResult(error='timeout') → LlmResult(ok=False, error='timeout')."""
        # Arrange
        driver, _ = make_driver(CliResult(error="timeout"))

        # Act
        result = await driver.complete(
            pool_id="p1",
            text="hi",
            model_cfg=make_model_cfg(),
            system_prompt="",
        )

        # Assert
        assert result.ok is False
        assert result.error == "timeout"
        assert result.result == ""

    async def test_complete_translates_warning(self) -> None:
        """CliResult(result='r', warning='truncated') → LlmResult with warning."""
        # Arrange
        driver, _ = make_driver(
            CliResult(result="r", warning="truncated", session_id="s")
        )

        # Act
        result = await driver.complete(
            pool_id="p1",
            text="hi",
            model_cfg=make_model_cfg(),
            system_prompt="",
        )

        # Assert
        assert result.warning == "truncated"
        assert result.result == "r"
        assert result.session_id == "s"
        assert result.ok is True

    async def test_messages_kwarg_ignored(self) -> None:
        """Passing messages=[...] does not raise; pool.send called with correct args."""
        # Arrange
        driver, pool = make_driver(CliResult(result="ok", session_id="s1"))
        model_cfg = make_model_cfg()

        # Act — should not raise
        result = await driver.complete(
            pool_id="my-pool",
            text="hello",
            model_cfg=model_cfg,
            system_prompt="",
            messages=[{"role": "user", "content": "hi"}],
        )

        # Assert — pool still called correctly (messages kwarg silently ignored)
        pool.send.assert_awaited_once_with("my-pool", "hello", model_cfg, "")
        assert result.ok is True


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


class TestClaudeCliDriverCapabilities:
    def test_capabilities(self) -> None:
        """Driver declares streaming=False and auth='oauth_only'."""
        driver, _ = make_driver(CliResult(result="ok"))
        assert driver.capabilities == {"streaming": False, "auth": "oauth_only"}
