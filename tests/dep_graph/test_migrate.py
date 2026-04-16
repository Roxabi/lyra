"""Tests for dep_graph.migrate.run_migrate() — RED phase (T4).

Verifies that run_migrate():
  - wraps bare ints into IssueRef objects
  - rewrites dict keys to "owner/repo#N"
  - is idempotent (no .new file + stdout message when already migrated)
  - completes partial migrations
  - never mutates the original file
"""

from __future__ import annotations

import json

# This import will fail initially — that's expected (RED)
from dep_graph.migrate import run_migrate


def _old_layout():
    """Representative pre-migration layout shape."""
    return {
        "meta": {
            "title": "T",
            "date": "2026-04-15",
            "repo": "Roxabi/lyra",  # old singular key
            "label_prefix": "graph:",
            "issue": 445,  # bare int
        },
        "lanes": [
            {
                "code": "a",
                "name": "A",
                "color": "a",
                "epic": {"issue": 605, "label": "x", "tag": "M1"},
                "order": [641, 642, 643],  # bare ints
                "par_groups": {"g0": [641, 642]},
                "bands": [{"before": 641, "text": "M0"}],
            }
        ],
        "standalone": {"order": [700, 701]},
        "overrides": {"641": {"title": "x"}, "700": {"size": "XS"}},
        "extra_deps": {
            "extra_blocked_by": {"641": [640]},
            "extra_blocking": {"640": [641]},
        },
        "cross_deps": [],
        "title_rules": [],
    }


def test_migrates_bare_ints_to_issue_refs(tmp_path):
    # Arrange
    p = tmp_path / "layout.json"
    p.write_text(json.dumps(_old_layout()))

    # Act
    result = run_migrate(p)
    assert result == 0

    # Assert
    new = p.with_suffix(p.suffix + ".new")
    assert new.exists()
    data = json.loads(new.read_text())

    # meta.repos[] replaces meta.repo
    assert "repo" not in data["meta"]
    assert data["meta"]["repos"] == ["Roxabi/lyra"]

    # lanes[].order items are IssueRef
    assert data["lanes"][0]["order"] == [
        {"repo": "Roxabi/lyra", "issue": 641},
        {"repo": "Roxabi/lyra", "issue": 642},
        {"repo": "Roxabi/lyra", "issue": 643},
    ]
    # standalone.order too
    assert data["standalone"]["order"] == [
        {"repo": "Roxabi/lyra", "issue": 700},
        {"repo": "Roxabi/lyra", "issue": 701},
    ]
    # par_groups inner arrays
    assert data["lanes"][0]["par_groups"]["g0"] == [
        {"repo": "Roxabi/lyra", "issue": 641},
        {"repo": "Roxabi/lyra", "issue": 642},
    ]
    # bands[].before wrapped
    assert data["lanes"][0]["bands"][0]["before"] == {
        "repo": "Roxabi/lyra",
        "issue": 641,
    }
    # meta.issue wrapped
    assert data["meta"]["issue"] == {"repo": "Roxabi/lyra", "issue": 445}


def test_migrates_override_keys_to_owner_repo_hash(tmp_path):
    # Arrange
    p = tmp_path / "layout.json"
    p.write_text(json.dumps(_old_layout()))

    # Act
    run_migrate(p)

    # Assert
    data = json.loads(p.with_suffix(p.suffix + ".new").read_text())

    # overrides keys rewritten
    assert "641" not in data["overrides"]
    assert data["overrides"]["Roxabi/lyra#641"] == {"title": "x"}
    assert data["overrides"]["Roxabi/lyra#700"] == {"size": "XS"}

    # extra_deps keys + inner arrays rewritten
    assert "641" not in data["extra_deps"]["extra_blocked_by"]
    assert data["extra_deps"]["extra_blocked_by"]["Roxabi/lyra#641"] == [
        "Roxabi/lyra#640"
    ]
    assert data["extra_deps"]["extra_blocking"]["Roxabi/lyra#640"] == [
        "Roxabi/lyra#641"
    ]


def test_idempotent_on_already_migrated(tmp_path, capsys):
    # Arrange — already-migrated layout
    already = {
        "meta": {
            "title": "T",
            "date": "2026-04-15",
            "repos": ["Roxabi/lyra"],
            "label_prefix": "graph:",
        },
        "lanes": [],
        "standalone": {"order": []},
        "overrides": {},
        "extra_deps": {"extra_blocked_by": {}, "extra_blocking": {}},
        "cross_deps": [],
        "title_rules": [],
    }
    p = tmp_path / "layout.json"
    p.write_text(json.dumps(already))

    # Act
    result = run_migrate(p)

    # Assert
    assert result == 0
    out = capsys.readouterr().out
    assert "Already migrated" in out
    # .new file should NOT be produced
    assert not p.with_suffix(p.suffix + ".new").exists()


def test_completes_partial_migration(tmp_path):
    # Arrange — mix of new `meta.repos` with leftover bare ints in lanes + overrides
    partial = {
        "meta": {
            "title": "T",
            "date": "2026-04-15",
            "repos": ["Roxabi/lyra"],
            "label_prefix": "graph:",
        },
        "lanes": [
            {
                "code": "a",
                "name": "A",
                "color": "a",
                "epic": {"issue": 605, "label": "x", "tag": "M1"},
                "order": [641],  # still bare int
                "par_groups": {},
                "bands": [],
            }
        ],
        "standalone": {"order": []},
        "overrides": {"641": {"title": "override"}},  # bare-int key leftover
        "extra_deps": {"extra_blocked_by": {}, "extra_blocking": {}},
        "cross_deps": [],
        "title_rules": [],
    }
    p = tmp_path / "layout.json"
    p.write_text(json.dumps(partial))

    # Act
    result = run_migrate(p)

    # Assert
    assert result == 0
    new = p.with_suffix(p.suffix + ".new")
    assert new.exists()
    data = json.loads(new.read_text())

    # lanes order wrapped
    assert data["lanes"][0]["order"] == [{"repo": "Roxabi/lyra", "issue": 641}]

    # overrides key rewritten
    assert "Roxabi/lyra#641" in data["overrides"]
    assert data["overrides"]["Roxabi/lyra#641"] == {"title": "override"}


def test_never_mutates_original(tmp_path):
    # Arrange
    p = tmp_path / "layout.json"
    original = _old_layout()
    p.write_text(json.dumps(original, indent=2))
    before = p.read_bytes()

    # Act
    run_migrate(p)

    # Assert
    after = p.read_bytes()
    assert before == after, "migrate must not mutate the original file"
