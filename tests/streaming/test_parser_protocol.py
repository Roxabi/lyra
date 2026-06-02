"""Tests for Parser[InT, OutT] Protocol shape (Phase 5 — #1282).

The Protocol is duck-typed and structural.  CliStreamingParser satisfies it at
the method-name level: it exposes feed, finalize, and is_done aliases (added in
#1667).  isinstance(CliStreamingParser(), Parser) therefore returns True and is
asserted below.  StreamProcessor still exposes only process (legacy public API)
and does not yet satisfy the Protocol; its conformance test is deferred.
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


class TestCliStreamingParserConformance:
    """Test 4 — CliStreamingParser satisfies Parser at runtime (#1667).

    Falsification: deleting any of the feed/finalize/is_done aliases on
    CliStreamingParser causes isinstance to return False and this test to fail.
    """

    def test_cli_streaming_parser_isinstance_parser(self) -> None:
        # Arrange
        from factory.core.cli.cli_streaming_parser import (
            CliStreamingParser,  # noqa: PLC0415
        )
        from factory.streaming import Parser  # noqa: PLC0415

        parser = CliStreamingParser(pool_id="test-pool")

        # Act + Assert — isinstance must return True because Parser is
        # @runtime_checkable and CliStreamingParser now exposes feed/finalize/is_done.
        assert isinstance(parser, Parser), (  # type: ignore[arg-type]
            "CliStreamingParser does not satisfy Parser Protocol — "
            "feed/finalize/is_done aliases missing"
        )
