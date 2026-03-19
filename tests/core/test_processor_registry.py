"""Tests for ProcessorRegistry (issue #363)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from lyra.core.processor_registry import (
    BaseProcessor,
    ProcessorRegistry,
)
from lyra.integrations.base import SessionTools

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_tools() -> SessionTools:
    scraper = MagicMock()
    vault = MagicMock()
    return SessionTools(scraper=scraper, vault=vault)


class _DummyProcessor(BaseProcessor):
    """Minimal concrete processor for registry tests."""

    async def pre(self, msg):
        return msg


class _AnotherProcessor(BaseProcessor):
    async def pre(self, msg):
        return msg


# ---------------------------------------------------------------------------
# ProcessorRegistry.register()
# ---------------------------------------------------------------------------


class TestRegister:
    def test_register_adds_command(self) -> None:
        # Arrange
        reg = ProcessorRegistry()

        # Act
        reg.register("/test")(_DummyProcessor)

        # Assert
        assert "/test" in reg.commands()

    def test_register_returns_class_unchanged(self) -> None:
        # Arrange
        reg = ProcessorRegistry()

        # Act
        result = reg.register("/test")(_DummyProcessor)

        # Assert
        assert result is _DummyProcessor

    def test_register_raises_on_duplicate(self) -> None:
        # Arrange
        reg = ProcessorRegistry()
        reg.register("/dup")(_DummyProcessor)

        # Act / Assert
        with pytest.raises(ValueError, match="already registered"):
            reg.register("/dup")(_AnotherProcessor)

    def test_register_with_description(self) -> None:
        # Arrange
        reg = ProcessorRegistry()

        # Act
        reg.register("/test", description="A test command")(_DummyProcessor)

        # Assert
        assert reg.descriptions()["/test"] == "A test command"


# ---------------------------------------------------------------------------
# ProcessorRegistry.get()
# ---------------------------------------------------------------------------


class TestGet:
    def test_get_returns_registered_class(self) -> None:
        # Arrange
        reg = ProcessorRegistry()
        reg.register("/get-test")(_DummyProcessor)

        # Act
        result = reg.get("/get-test")

        # Assert
        assert result is _DummyProcessor

    def test_get_returns_none_for_unknown(self) -> None:
        # Arrange
        reg = ProcessorRegistry()

        # Act
        result = reg.get("/nonexistent")

        # Assert
        assert result is None


# ---------------------------------------------------------------------------
# ProcessorRegistry.build()
# ---------------------------------------------------------------------------


class TestBuild:
    def test_build_returns_none_for_unknown_command(self) -> None:
        # Arrange
        reg = ProcessorRegistry()
        tools = make_tools()

        # Act
        result = reg.build("/unknown", tools)

        # Assert
        assert result is None

    def test_build_returns_fresh_instance_each_call(self) -> None:
        # Arrange
        reg = ProcessorRegistry()
        reg.register("/fresh")(_DummyProcessor)
        tools = make_tools()

        # Act
        p1 = reg.build("/fresh", tools)
        p2 = reg.build("/fresh", tools)

        # Assert
        assert p1 is not None
        assert p2 is not None
        assert p1 is not p2

    def test_build_injects_tools(self) -> None:
        # Arrange
        reg = ProcessorRegistry()
        reg.register("/tools-test")(_DummyProcessor)
        tools = make_tools()

        # Act
        proc = reg.build("/tools-test", tools)

        # Assert
        assert proc is not None
        assert proc.tools is tools

    def test_build_returns_instance_of_registered_class(self) -> None:
        # Arrange
        reg = ProcessorRegistry()
        reg.register("/cls-test")(_DummyProcessor)
        tools = make_tools()

        # Act
        proc = reg.build("/cls-test", tools)

        # Assert
        assert isinstance(proc, _DummyProcessor)


# ---------------------------------------------------------------------------
# ProcessorRegistry.clear()
# ---------------------------------------------------------------------------


class TestClear:
    def test_clear_empties_registry(self) -> None:
        # Arrange
        reg = ProcessorRegistry()
        reg.register("/a")(_DummyProcessor)
        reg.register("/b")(_AnotherProcessor)

        # Act
        reg.clear()

        # Assert
        assert reg.commands() == set()

    def test_clear_allows_reregistration(self) -> None:
        # Arrange
        reg = ProcessorRegistry()
        reg.register("/reuse")(_DummyProcessor)
        reg.clear()

        # Act — should not raise
        reg.register("/reuse")(_AnotherProcessor)

        # Assert
        assert reg.get("/reuse") is _AnotherProcessor


# ---------------------------------------------------------------------------
# ProcessorRegistry.commands() and descriptions()
# ---------------------------------------------------------------------------


class TestCommandsAndDescriptions:
    def test_commands_returns_set_of_names(self) -> None:
        # Arrange
        reg = ProcessorRegistry()
        reg.register("/cmd-a")(_DummyProcessor)
        reg.register("/cmd-b")(_AnotherProcessor)

        # Act
        cmds = reg.commands()

        # Assert
        assert cmds == {"/cmd-a", "/cmd-b"}

    def test_descriptions_returns_mapping(self) -> None:
        # Arrange
        reg = ProcessorRegistry()
        reg.register("/d1", description="Desc one")(_DummyProcessor)
        reg.register("/d2", description="Desc two")(_AnotherProcessor)

        # Act
        descs = reg.descriptions()

        # Assert
        assert descs == {"/d1": "Desc one", "/d2": "Desc two"}


# ---------------------------------------------------------------------------
# Module-level singleton: importing lyra.core.processors registers built-ins
# ---------------------------------------------------------------------------


class TestModuleSingletonRegistration:
    def test_import_registers_expected_commands(self) -> None:
        """Importing lyra.core.processors populates the module singleton."""
        import importlib

        import lyra.core.processors
        import lyra.core.processors.explain
        import lyra.core.processors.search
        import lyra.core.processors.summarize
        import lyra.core.processors.vault_add
        from lyra.core.processor_registry import registry

        # Arrange — clear then force reload to re-trigger @register decorators.
        # Needed because a preceding conftest may have called registry.clear() after
        # the modules were already imported (Python caches them and skips re-execution).
        registry.clear()
        importlib.reload(lyra.core.processors.explain)
        importlib.reload(lyra.core.processors.search)
        importlib.reload(lyra.core.processors.summarize)
        importlib.reload(lyra.core.processors.vault_add)
        importlib.reload(lyra.core.processors)

        # Act
        registered = registry.commands()

        # Assert — all four built-in commands are present
        expected = {"/vault-add", "/explain", "/summarize", "/search"}
        assert expected.issubset(registered), (
            f"Missing commands: {expected - registered}"
        )
