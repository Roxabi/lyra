"""Shared key helpers for gh.json owner/repo#N format."""

from __future__ import annotations

import re

_KEY_RE = re.compile(r"^(?P<repo>[^/]+/[^/]+)#(?P<issue>\d+)$")


def parse_key(key: str) -> tuple[str, int]:
    """Parse 'owner/repo#N' into (repo, issue_num)."""
    m = _KEY_RE.match(key)
    if not m:
        raise ValueError(f"Invalid gh.json key: {key!r}")
    return m.group("repo"), int(m.group("issue"))


def format_key(repo: str, issue: int) -> str:
    """Format (repo, issue_num) as 'owner/repo#N'."""
    return f"{repo}#{issue}"


def repo_slug(repo: str) -> str:
    """Convert 'Owner/repo-name' to a CSS-safe slug: 'owner-repo-name'."""
    return repo.replace("/", "-").replace("_", "-").lower()
