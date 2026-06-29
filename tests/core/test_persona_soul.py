"""Tests for AgentSoul v1 markdown parse/merge/compose."""

from __future__ import annotations

import pytest

from factory.core.persona import (
    SOUL_SECTION_ORDER,
    compose_soul_document_from_markdown,
    merge_soul_sections,
    parse_soul_markdown,
    validate_soul_document_bytes,
)

_SAMPLE_MD = """## Identity
I am Lyra.
## Personality
Helpful.
## Values
Never harm.
## Expertise
Code.
## Guidelines
Be direct.
"""


class TestParseSoulMarkdown:
    def test_five_sections(self) -> None:
        secs = parse_soul_markdown(_SAMPLE_MD)
        assert set(secs.keys()) == set(SOUL_SECTION_ORDER)
        assert "Lyra" in secs["Identity"]

    def test_unknown_section_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown soul section"):
            parse_soul_markdown("## Foo\nbar")


class TestComposeSoulDocument:
    def test_includes_voice_instruction(self) -> None:
        composed = compose_soul_document_from_markdown(_SAMPLE_MD)
        assert "Lyra" in composed
        assert "Voice messages" in composed
        assert len(composed.encode()) < 65536

    def test_merge_roundtrip(self) -> None:
        secs = parse_soul_markdown(_SAMPLE_MD)
        md = merge_soul_sections(secs)
        again = parse_soul_markdown(md)
        assert again["Identity"] == secs["Identity"]


class TestValidateSoulDocumentBytes:
    def test_rejects_oversize(self) -> None:
        with pytest.raises(ValueError, match="exceeds"):
            validate_soul_document_bytes(b"x" * (48 * 1024 + 1))