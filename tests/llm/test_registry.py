"""Tests for ProviderRegistry.

RED phase — these tests will fail until S1 implementation lands.
Source: src/factory/llm/registry.py

Integration tests: real ProviderRegistry wired with mock drivers.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from factory.llm.registry import ProviderRegistry

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
        registry.register("claude-cli", driver)
        result = registry.get("claude-cli")

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
        registry.register("nats", make_mock_driver())

        # Act / Assert
        with pytest.raises(KeyError) as exc_info:
            registry.get("ollama")

        error_msg = str(exc_info.value)
        assert "ollama" in error_msg
        # Registered backends are no longer leaked in the error message
        assert "claude-cli" not in error_msg
        assert "nats" not in error_msg

    def test_register_and_get_omp_rpc(self) -> None:
        """omp-rpc can be registered and retrieved; unregistered key raises KeyError.

        T6: Confirms ProviderRegistry is backend-agnostic — the generic
        register/get mechanism works for the omp-rpc key before any
        specialised wiring is added.  get() must raise KeyError (not
        ValueError) for an unregistered backend.
        """
        # Arrange
        registry = ProviderRegistry()
        omp_driver = make_mock_driver()

        # Act — register omp-rpc and retrieve it
        registry.register("omp-rpc", omp_driver)
        result = registry.get("omp-rpc")

        # Assert — round-trip
        assert result is omp_driver

        # Assert — unregistered key raises KeyError (not ValueError)
        with pytest.raises(KeyError) as exc_info:
            registry.get("unregistered-backend")

        assert type(exc_info.value) is KeyError
        assert "unregistered-backend" in str(exc_info.value)
