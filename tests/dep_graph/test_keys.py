"""Tests for dep_graph.keys — owner/repo#N key helpers."""

from __future__ import annotations

import pytest
from dep_graph.keys import format_key, parse_key, repo_slug

# parse_key ------------------------------------------------------------------


def test_parse_key_happy_path():
    assert parse_key("Roxabi/lyra#641") == ("Roxabi/lyra", 641)


def test_parse_key_handles_hyphens_in_repo():
    assert parse_key("Roxabi/roxabi-vault#24") == ("Roxabi/roxabi-vault", 24)


def test_parse_key_rejects_bare_int():
    with pytest.raises(ValueError, match="Invalid gh.json key"):
        parse_key("641")


def test_parse_key_rejects_missing_hash():
    with pytest.raises(ValueError, match="Invalid gh.json key"):
        parse_key("Roxabi/lyra-641")


def test_parse_key_rejects_missing_repo():
    with pytest.raises(ValueError, match="Invalid gh.json key"):
        parse_key("#641")


def test_parse_key_rejects_non_numeric_issue():
    with pytest.raises(ValueError, match="Invalid gh.json key"):
        parse_key("Roxabi/lyra#abc")


def test_parse_key_rejects_extra_path_segments():
    with pytest.raises(ValueError, match="Invalid gh.json key"):
        parse_key("Roxabi/foo/bar#1")


# format_key -----------------------------------------------------------------


def test_format_key_happy_path():
    assert format_key("Roxabi/lyra", 641) == "Roxabi/lyra#641"


def test_format_key_handles_hyphens():
    assert format_key("Roxabi/roxabi-vault", 24) == "Roxabi/roxabi-vault#24"


# round-trip -----------------------------------------------------------------


def test_round_trip_preserves_values():
    for repo, issue in [
        ("Roxabi/lyra", 641),
        ("Roxabi/roxabi-vault", 24),
        ("Owner/project_name", 1),
    ]:
        assert parse_key(format_key(repo, issue)) == (repo, issue)


# repo_slug ------------------------------------------------------------------


def test_repo_slug_lowercases():
    assert repo_slug("Roxabi/lyra") == "roxabi-lyra"


def test_repo_slug_replaces_slash_with_dash():
    assert repo_slug("Roxabi/roxabi-vault") == "roxabi-roxabi-vault"


def test_repo_slug_replaces_underscore_with_dash():
    assert repo_slug("Owner/some_repo") == "owner-some-repo"
