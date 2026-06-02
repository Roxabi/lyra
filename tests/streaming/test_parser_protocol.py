"""Tests for Parser[InT, OutT] Protocol shape (Phase 5 — #1282).

The Protocol is duck-typed and structural; consumer classes (CliStreamingParser,
StreamProcessor) do NOT implement feed/finalize/is_done method names verbatim —
they expose parse_line/process per the legacy public API. Runtime isinstance()
conformance is therefore NOT asserted; tests below verify the Protocol itself
is well-formed (importable, generic, method names declared). When consumers
gain feed/finalize/is_done aliases in a future issue, an isinstance-based
conformance test can be added; until then, this file documents the shape only.
"""

from __future__ import annotations

import typing

import pytest


class TestParserProtocolImport:
    """Test 1 — Parser is importable and is a Protocol subclass."""

    def test_parser_importable_and_is_protocol(self) -> None:
        # Arrange + Act
        from factory.streaming import Parser  # noqa: PLC0415

        # Assert
        assert issubclass(Parser, typing.Protocol)  # type: ignore[arg-type]


class TestParserTypeParameters:
    """Test 2 — Parser is generic with 2 type vars (InT, OutT)."""

    def test_parser_has_two_type_parameters(self) -> None:
        # Arrange
        from factory.streaming import Parser  # noqa: PLC0415

        # Act — __parameters__ is a runtime attribute of Generic classes
        params = Parser.__parameters__  # type: ignore[attr-defined]

        # Assert
        assert len(params) == 2, (
            f"Expected 2 type parameters (InT, OutT), got {len(params)}: {params}"
        )


class TestParserMethodNames:
    """Test 3 — Protocol declares feed, finalize, and is_done."""

    @pytest.mark.parametrize("method_name", ["feed", "finalize", "is_done"])
    def test_parser_declares_required_method(self, method_name: str) -> None:
        # Arrange
        from factory.streaming import Parser  # noqa: PLC0415

        # Act + Assert
        assert hasattr(Parser, method_name), (
            f"Parser Protocol is missing required method: {method_name!r}"
        )
