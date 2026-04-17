"""Unit tests for dep_graph.fetch — label-parsing helpers.

All tests are pure input/output; no filesystem or network access.
"""

from __future__ import annotations

from dep_graph.fetch import _derive_size_from_labels

# ---------------------------------------------------------------------------
# _derive_size_from_labels tests
# ---------------------------------------------------------------------------


def test_derive_size_empty_list_returns_none():
    """Empty label list → None."""
    assert _derive_size_from_labels([]) is None


def test_derive_size_no_size_prefix_returns_none():
    """Labels present but none with 'size:' prefix → None."""
    assert _derive_size_from_labels(["bug", "priority:high", "graph:lane/x"]) is None


def test_derive_size_valid_label_returns_value():
    """Single 'size:M' label → 'M'."""
    assert _derive_size_from_labels(["size:M"]) == "M"


def test_derive_size_multiple_size_labels_returns_first():
    """Multiple 'size:*' labels → first one wins."""
    assert _derive_size_from_labels(["size:S", "size:L"]) == "S"
