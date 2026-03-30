"""Tests for CommandParser and CommandContext (#153)."""

from __future__ import annotations

import pytest

from lyra.core.commands.command_parser import CommandParser


@pytest.fixture
def parser() -> CommandParser:
    return CommandParser()


class TestCommandParserSlashPrefix:
    def test_slash_with_args(self, parser: CommandParser) -> None:
        # Arrange / Act
        ctx = parser.parse("/imagine a cat")

        # Assert
        assert ctx is not None
        assert ctx.prefix == "/"
        assert ctx.name == "imagine"
        assert ctx.args == "a cat"
        assert ctx.raw == "/imagine a cat"

    def test_slash_no_args(self, parser: CommandParser) -> None:
        # Arrange / Act
        ctx = parser.parse("/help")

        # Assert
        assert ctx is not None
        assert ctx.prefix == "/"
        assert ctx.name == "help"
        assert ctx.args == ""

    def test_slash_name_lowercased(self, parser: CommandParser) -> None:
        # Arrange / Act
        ctx = parser.parse("/CMD args")

        # Assert
        assert ctx is not None
        assert ctx.name == "cmd"

    def test_slash_alone_returns_none(self, parser: CommandParser) -> None:
        # Arrange / Act / Assert
        assert parser.parse("/") is None

    def test_slash_with_space_only_returns_none(self, parser: CommandParser) -> None:
        # Arrange / Act / Assert — space after / = not a command
        assert parser.parse("/ something") is None


class TestCommandParserBangPrefix:
    def test_bang_with_args(self, parser: CommandParser) -> None:
        # Arrange / Act
        ctx = parser.parse("!cmd args")

        # Assert
        assert ctx is not None
        assert ctx.prefix == "!"
        assert ctx.name == "cmd"
        assert ctx.args == "args"
        assert ctx.raw == "!cmd args"

    def test_bang_no_args(self, parser: CommandParser) -> None:
        # Arrange / Act
        ctx = parser.parse("!help")

        # Assert
        assert ctx is not None
        assert ctx.prefix == "!"
        assert ctx.name == "help"
        assert ctx.args == ""

    def test_bang_alone_returns_none(self, parser: CommandParser) -> None:
        # Arrange / Act / Assert
        assert parser.parse("!") is None


class TestCommandParserNonCommands:
    def test_plain_text_returns_none(self, parser: CommandParser) -> None:
        # Arrange / Act / Assert
        assert parser.parse("hello world") is None

    def test_empty_string_returns_none(self, parser: CommandParser) -> None:
        # Arrange / Act / Assert
        assert parser.parse("") is None

    def test_whitespace_only_returns_none(self, parser: CommandParser) -> None:
        # Arrange / Act / Assert
        assert parser.parse("   ") is None

    def test_text_without_prefix_returns_none(self, parser: CommandParser) -> None:
        # Arrange / Act / Assert
        assert parser.parse("imagine a cat") is None


class TestCommandContextImmutability:
    def test_context_is_frozen(self, parser: CommandParser) -> None:
        # Arrange
        ctx = parser.parse("/test")
        assert ctx is not None

        # Act / Assert — frozen dataclass must raise on attribute assignment
        with pytest.raises((AttributeError, TypeError)):
            setattr(ctx, "name", "other")
