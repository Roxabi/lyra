"""Tests for _parse_title() and _parse_tags() helper functions."""

from __future__ import annotations

from lyra.core.processors.vault_add import _parse_tags, _parse_title

# ---------------------------------------------------------------------------
# _parse_title()
# ---------------------------------------------------------------------------


class TestParseTitle:
    def test_parses_title_line(self) -> None:
        # Arrange
        text = "Title: My Great Page\nTags: foo, bar\nSome body text."

        # Act
        result = _parse_title(text, fallback="fallback-url")

        # Assert
        assert result == "My Great Page"

    def test_falls_back_when_no_title_line(self) -> None:
        # Arrange
        text = "No title here, just some content."

        # Act
        result = _parse_title(text, fallback="https://example.com")

        # Assert
        assert result == "https://example.com"

    def test_strips_whitespace_from_title(self) -> None:
        # Arrange
        text = "Title:   Trimmed Title   \nBody."

        # Act
        result = _parse_title(text, fallback="fallback")

        # Assert
        assert result == "Trimmed Title"

    def test_handles_empty_text(self) -> None:
        # Arrange / Act
        result = _parse_title("", fallback="fallback-url")

        # Assert
        assert result == "fallback-url"

    def test_multiline_text_matches_first_title(self) -> None:
        # Arrange
        text = "Intro paragraph.\nTitle: Correct Title\nMore text.\nTitle: Second"

        # Act
        result = _parse_title(text, fallback="fallback")

        # Assert
        assert result == "Correct Title"


# ---------------------------------------------------------------------------
# _parse_tags()
# ---------------------------------------------------------------------------


class TestParseTags:
    def test_parses_comma_separated_tags(self) -> None:
        # Arrange
        text = "Title: Page\nTags: foo, bar, baz\nBody."

        # Act
        result = _parse_tags(text)

        # Assert
        assert result == ["foo", "bar", "baz"]

    def test_returns_empty_list_when_no_tags_line(self) -> None:
        # Arrange
        text = "Title: Page\nNo tags here."

        # Act
        result = _parse_tags(text)

        # Assert
        assert result == []

    def test_strips_whitespace_from_each_tag(self) -> None:
        # Arrange
        text = "Tags:  python ,  asyncio ,  testing  "

        # Act
        result = _parse_tags(text)

        # Assert
        assert result == ["python", "asyncio", "testing"]

    def test_handles_empty_text(self) -> None:
        # Arrange / Act
        result = _parse_tags("")

        # Assert
        assert result == []

    def test_ignores_empty_segments(self) -> None:
        # Arrange — trailing comma produces empty segment
        text = "Tags: alpha, beta, "

        # Act
        result = _parse_tags(text)

        # Assert
        assert result == ["alpha", "beta"]

    def test_single_tag(self) -> None:
        # Arrange
        text = "Tags: only-one"

        # Act
        result = _parse_tags(text)

        # Assert
        assert result == ["only-one"]
