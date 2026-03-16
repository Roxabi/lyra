import pytest

from lyra.adapters._shared import chunk_text


class TestChunkText:
    def test_empty_string_returns_empty_list(self) -> None:
        assert chunk_text("", 10) == []

    def test_short_text_single_chunk(self) -> None:
        assert chunk_text("hello", 10) == ["hello"]

    def test_text_split_at_exact_boundary(self) -> None:
        assert chunk_text("abcdef", 3) == ["abc", "def"]

    def test_text_longer_than_max_len(self) -> None:
        result = chunk_text("a" * 10, 3)
        assert result == ["aaa", "aaa", "aaa", "a"]

    def test_max_len_of_one(self) -> None:
        assert chunk_text("abc", 1) == ["a", "b", "c"]

    def test_invalid_max_len_raises(self) -> None:
        with pytest.raises(ValueError, match="max_len"):
            chunk_text("x", 0)
        with pytest.raises(ValueError, match="max_len"):
            chunk_text("x", -1)

    def test_escape_fn_applied_before_chunking(self) -> None:
        # escape_fn doubles every character — max_len applies to post-escape length
        result = chunk_text("abc", 4, escape_fn=lambda t: "".join(c * 2 for c in t))
        # "aabbcc" split at 4 → ["aabb", "cc"]
        assert result == ["aabb", "cc"]

    def test_escape_fn_on_empty_returns_empty(self) -> None:
        assert chunk_text("", 10, escape_fn=str.upper) == []

    def test_no_escape_fn(self) -> None:
        # chunk_text splits at word boundaries, so "hello world" splits at the
        # space — each chunk is rstrip'd/lstrip'd, yielding two clean words.
        assert chunk_text("hello world", 5) == ["hello", "world"]
