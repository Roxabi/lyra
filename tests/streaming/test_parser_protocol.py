"""Tests for Parser[InT, OutT] Protocol conformance scaffold (Phase 5 — #1282).

Wave 1 / Slice 1 tests.  The consumer classes (CliStreamingParser, StreamProcessor)
do NOT yet expose `feed` / `finalize` / `is_done` — those are wired in Slices 2–4.
Tests here confirm the Protocol itself is well-formed; the isinstance conformance
check is skipped with an explicit marker so future slices can unskip it.
"""

from __future__ import annotations

import typing

import pytest


class TestParserProtocolImport:
    """Test 1 — Parser is importable and is a Protocol subclass."""

    def test_parser_importable_and_is_protocol(self) -> None:
        # Arrange + Act
        from lyra.streaming import Parser  # noqa: PLC0415

        # Assert
        assert issubclass(Parser, typing.Protocol)  # type: ignore[arg-type]


class TestParserTypeParameters:
    """Test 2 — Parser is generic with 2 type vars (InT, OutT)."""

    def test_parser_has_two_type_parameters(self) -> None:
        # Arrange
        from lyra.streaming import Parser  # noqa: PLC0415

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
        from lyra.streaming import Parser  # noqa: PLC0415

        # Act + Assert
        assert hasattr(Parser, method_name), (
            f"Parser Protocol is missing required method: {method_name!r}"
        )


class TestParserConformancePlaceholder:
    """Test 4 — Forward-compat: isinstance conformance (wired in Slice 2/3)."""

    @pytest.mark.skip(reason="Slice 2/3 wires the protocol-aligned methods")
    def test_cli_streaming_parser_satisfies_parser_protocol(self) -> None:
        from lyra.core.cli.cli_streaming_parser import (  # noqa: PLC0415
            CliStreamingParser,
        )
        from lyra.streaming import Parser  # noqa: PLC0415

        instance = CliStreamingParser(pool_id="test-pool")
        assert isinstance(instance, Parser)  # type: ignore[misc]

    @pytest.mark.skip(reason="Slice 2/3 wires the protocol-aligned methods")
    def test_stream_processor_satisfies_parser_protocol(self) -> None:
        from lyra.core.messaging.tool_display_config import (  # noqa: PLC0415
            ToolDisplayConfig,
        )
        from lyra.core.processors.stream_processor import (  # noqa: PLC0415
            StreamProcessor,
        )
        from lyra.streaming import Parser  # noqa: PLC0415

        # StreamProcessor.__init__ requires a config; use a minimal real instance.
        config = ToolDisplayConfig()
        instance = StreamProcessor(config=config)
        assert isinstance(instance, Parser)  # type: ignore[misc]
