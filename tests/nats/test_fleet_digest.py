"""Fleet image digest compare + state load (Block 6)."""

from __future__ import annotations

import json
from pathlib import Path

from factory.nats.fleet_digest import (
    compare_image_digests,
    load_fleet_digest_state,
)


def test_compare_image_digests_current() -> None:
    assert compare_image_digests("abc123", "abc123") == "current"


def test_compare_image_digests_stale() -> None:
    assert compare_image_digests("abc123", "def456") == "stale"


def test_compare_image_digests_unknown_compare() -> None:
    assert compare_image_digests(None, "abc") == "unknown_compare"
    assert compare_image_digests("abc", None) == "unknown_compare"


def test_load_fleet_digest_state(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "fleet-digests.json"
    path.write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "container_name": "factory-hub",
                        "image_ref": "ghcr.io/roxabi/factory:staging-svc",
                        "running_digest": "aaa",
                        "registry_digest": "bbb",
                        "status": "stale",
                        "checked_at": "2026-06-30T10:00:00+00:00",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("FLEET_DIGEST_STATE_PATH", str(path))
    rows = load_fleet_digest_state()
    assert "factory-hub" in rows
    assert rows["factory-hub"].status == "stale"
    assert rows["factory-hub"].running_digest == "aaa"