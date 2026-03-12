"""Tests for ProviderRegistry.

RED phase — these tests will fail until S1 implementation lands.
Source: src/lyra/llm/registry.py

Integration tests: real ProviderRegistry wired with mock drivers.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from lyra.llm.registry import ProviderRegistry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_mock_driver() -> MagicMock:
    driver = MagicMock()
    driver.capabilities = {"streaming": False, "auth": "api_key"}
    return driver


# ---------------------------------------------------------------------------
# TestProviderRegistry
# ---------------------------------------------------------------------------


class TestProviderRegistry:
    def test_register_and_get(self) -> None:
        """register() + get() returns the registered driver."""
        # Arrange
        registry = ProviderRegistry()
        driver = make_mock_driver()

        # Act
        registry.register("anthropic-sdk", driver)
        result = registry.get("anthropic-sdk")

        # Assert
        assert result is driver

    def test_get_keyerror_unregistered(self) -> None:
        """get() raises KeyError containing backend name when not registered."""
        # Arrange
        registry = ProviderRegistry()
        registry.register("claude-cli", make_mock_driver())

        # Act / Assert
        with pytest.raises(KeyError) as exc_info:
            registry.get("ollama")

        assert "ollama" in str(exc_info.value)

    def test_get_keyerror_message_format(self) -> None:
        """KeyError lists registered backends sorted alphabetically."""
        # Arrange
        registry = ProviderRegistry()
        registry.register("claude-cli", make_mock_driver())
        registry.register("anthropic-sdk", make_mock_driver())

        # Act / Assert
        with pytest.raises(KeyError) as exc_info:
            registry.get("ollama")

        error_msg = str(exc_info.value)
        # All three names appear in the error
        assert "ollama" in error_msg
        assert "anthropic-sdk" in error_msg
        assert "claude-cli" in error_msg
        # Registered backends are sorted: anthropic-sdk before claude-cli
        assert error_msg.index("anthropic-sdk") < error_msg.index("claude-cli")
