"""Tests for extract_patch() — valid, missing, and multi-line cases."""

from __future__ import annotations

import asyncio

import pytest

from lyra.core.agent_refiner import RefinementPatch
from lyra.core.agent_refiner_stages import extract_patch


@pytest.fixture(autouse=True)
def _restore_event_loop():  # noqa: F841  # autouse fixture
    """Restore a fresh event loop after each test."""
    yield
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)


# ---------------------------------------------------------------------------
# T7 — extract_patch valid
# ---------------------------------------------------------------------------


class TestExtractPatchValid:
    """extract_patch() — extracts embedded JSON patch block."""

    def test_extract_patch_returns_patch_from_valid_block(self) -> None:
        # Arrange
        text = 'Some text <<PATCH>>\n{"voice_json": "test"}\n<<END_PATCH>>'

        # Act
        result = extract_patch(text, RefinementPatch)

        # Assert
        assert result is not None
        assert isinstance(result, RefinementPatch)
        assert result.fields == {"voice_json": "test"}

    def test_extract_patch_with_multiple_fields(self) -> None:
        # Arrange
        text = (
            "Here are the changes:\n"
            '<<PATCH>>\n{"model": "claude-opus-4-6", "streaming": true}\n<<END_PATCH>>'
        )

        # Act
        result = extract_patch(text, RefinementPatch)

        # Assert
        assert result is not None
        assert result.fields["model"] == "claude-opus-4-6"
        assert result.fields["streaming"] is True

    def test_extract_patch_inline_block(self) -> None:
        # Arrange -- patch block on a single line
        text = '<<PATCH>>{"persona_json": "aryl"}<<END_PATCH>>'

        # Act
        result = extract_patch(text, RefinementPatch)

        # Assert
        assert result is not None
        assert result.fields == {"persona_json": "aryl"}

    def test_extract_patch_with_surrounding_text(self) -> None:
        # Arrange
        text = (
            "I'll update the following fields:\n"
            '<<PATCH>>\n{"fallback_language": "fr"}\n<<END_PATCH>>\n'
            "Please confirm these changes."
        )

        # Act
        result = extract_patch(text, RefinementPatch)

        # Assert
        assert result is not None
        assert result.fields == {"fallback_language": "fr"}


# ---------------------------------------------------------------------------
# T8 — extract_patch missing
# ---------------------------------------------------------------------------


class TestExtractPatchMissing:
    """extract_patch() — returns None when no patch block present."""

    def test_extract_patch_returns_none_when_no_block(self) -> None:
        # Arrange
        text = "No patch here"

        # Act
        result = extract_patch(text, RefinementPatch)

        # Assert
        assert result is None

    def test_extract_patch_returns_none_for_empty_string(self) -> None:
        # Arrange + Act + Assert
        assert extract_patch("", RefinementPatch) is None

    def test_extract_patch_returns_none_for_malformed_block(self) -> None:
        # Arrange — markers present but JSON is invalid
        text = "<<PATCH>>\nnot valid json\n<<END_PATCH>>"

        # Act
        result = extract_patch(text, RefinementPatch)

        # Assert
        assert result is None

    def test_extract_patch_returns_none_for_non_dict_json(self) -> None:
        # Arrange — valid JSON but not a dict
        text = '<<PATCH>>\n["list", "not", "dict"]\n<<END_PATCH>>'

        # Act
        result = extract_patch(text, RefinementPatch)

        # Assert
        assert result is None

    def test_extract_patch_returns_none_when_only_start_marker(self) -> None:
        # Arrange — missing end marker
        text = '<<PATCH>>\n{"model": "new"}'

        # Act
        result = extract_patch(text, RefinementPatch)

        # Assert
        assert result is None


# ---------------------------------------------------------------------------
# T11 — extract_patch multi-line / pretty-printed JSON (SC-7)
# ---------------------------------------------------------------------------


class TestExtractPatchMultiLine:
    """extract_patch() — handles pretty-printed and complex JSON."""

    def test_extract_patch_handles_pretty_printed_json(self) -> None:
        # Arrange — LLMs commonly emit pretty-printed JSON
        text = (
            "I'll update the model and persona.\n"
            "<<PATCH>>\n"
            '{\n  "model": "claude-opus-4-6",\n  "persona_json": "aryl"\n}\n'
            "<<END_PATCH>>\n"
            "These changes will take effect on restart."
        )

        # Act
        result = extract_patch(text, RefinementPatch)

        # Assert
        assert result is not None
        assert result.fields == {"model": "claude-opus-4-6", "persona_json": "aryl"}

    def test_extract_patch_handles_json_with_boolean_values(self) -> None:
        # Arrange — JSON with booleans (a common LLM output pattern)
        text = '<<PATCH>>\n{"model": "new", "streaming": true}\n<<END_PATCH>>'

        # Act
        result = extract_patch(text, RefinementPatch)

        # Assert
        assert result is not None
        assert result.fields["model"] == "new"
        assert result.fields["streaming"] is True
