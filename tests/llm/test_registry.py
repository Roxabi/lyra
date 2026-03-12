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
        """KeyError mentions the unknown backend but not the registered list."""
        # Arrange
        registry = ProviderRegistry()
        registry.register("claude-cli", make_mock_driver())
        registry.register("anthropic-sdk", make_mock_driver())

        # Act / Assert
        with pytest.raises(KeyError) as exc_info:
            registry.get("ollama")

        error_msg = str(exc_info.value)
        assert "ollama" in error_msg
        # Registered backends are no longer leaked in the error message
        assert "anthropic-sdk" not in error_msg
        assert "claude-cli" not in error_msg
