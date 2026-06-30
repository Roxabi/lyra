"""ACL matrix regression test for the gh-helper identity (#1082).

Guards the least-privilege NATS contract for gh-helper:
- publish: fleet container_report + factory.gh.mint_failure.>
- subscribe: [] — no subscribe permissions
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MATRIX = REPO / "deploy" / "nats" / "acl-matrix.json"


def _identities() -> dict:
    assert MATRIX.exists(), f"acl-matrix.json missing: {MATRIX}"
    return json.loads(MATRIX.read_text())["identities"]


def test_gh_helper_publish_subjects() -> None:
    """gh-helper may publish only to factory.gh.mint_failure.> (least-privilege)."""
    identities = _identities()
    assert "gh-helper" in identities, (
        "gh-helper identity must be present in acl-matrix.json"
    )
    assert identities["gh-helper"]["publish"] == [
        "factory.metric.host.container_report",
        "factory.gh.mint_failure.>",
    ]


def test_gh_helper_subscribe_is_empty() -> None:
    """gh-helper must have no subscribe permissions (publish-only identity)."""
    identities = _identities()
    assert "gh-helper" in identities, (
        "gh-helper identity must be present in acl-matrix.json"
    )
    assert identities["gh-helper"]["subscribe"] == []
