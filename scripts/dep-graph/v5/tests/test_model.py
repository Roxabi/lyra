"""Tests for v5.data.model — dataclass invariants and static config."""
from __future__ import annotations

import pytest

from v5.data.model import (
    COLUMN_GROUPS,
    MILESTONES,
    MS_CODES,
    EpicMeta,
    GraphData,
    Lane,
    ref_key,
)

# ─── COLUMN_GROUPS ───────────────────────────────────────────────────────────

class TestColumnGroups:
    def test_has_fifteen_entries(self):
        assert len(COLUMN_GROUPS) == 15

    def test_labels_are_unique(self):
        labels = [label for label, _, _ in COLUMN_GROUPS]
        assert len(set(labels)) == len(labels)

    def test_tone_keys_are_present(self):
        for label, tone, codes in COLUMN_GROUPS:
            assert isinstance(tone, str) and len(tone) > 0

    def test_each_entry_has_lane_codes(self):
        for _, _, codes in COLUMN_GROUPS:
            assert len(codes) >= 1

    def test_known_columns_present(self):
        labels = {label for label, _, _ in COLUMN_GROUPS}
        assert "NATS" in labels
        assert "HUB" in labels
        assert "VOICE" in labels
        assert "DEPLOY" in labels
        assert "VAULT" in labels


# ─── MILESTONES ──────────────────────────────────────────────────────────────

class TestMilestones:
    def test_has_twelve_entries(self):
        assert len(MILESTONES) == 12

    def test_codes_cover_m0_through_fin(self):
        codes = [code for _, code, _ in MILESTONES]
        assert codes == [
            "M0", "M1", "M2", "M3", "M4", "M5",
            "M6", "M7", "M8", "M9", "M10", "FIN",
        ]

    def test_ms_codes_list_matches(self):
        codes_from_list = [code for _, code, _ in MILESTONES]
        assert MS_CODES == codes_from_list

    def test_short_names_non_empty(self):
        for _, _, short in MILESTONES:
            assert isinstance(short, str) and len(short) > 0

    def test_full_labels_non_empty(self):
        for label, _, _ in MILESTONES:
            assert isinstance(label, str) and len(label) > 0


# ─── EpicMeta ────────────────────────────────────────────────────────────────

class TestEpicMeta:
    def test_frozen(self):
        meta = EpicMeta(issue=1, label="test", tag="M0")
        with pytest.raises((AttributeError, TypeError)):
            meta.issue = 2  # type: ignore[misc]

    def test_none_issue(self):
        meta = EpicMeta(issue=None, label="no issue", tag="")
        assert meta.issue is None

    def test_fields_accessible(self):
        meta = EpicMeta(issue=42, label="my epic", tag="M1-TAG")
        assert meta.issue == 42
        assert meta.label == "my epic"
        assert meta.tag == "M1-TAG"


# ─── Lane ────────────────────────────────────────────────────────────────────

class TestLane:
    def test_frozen(self):
        lane = Lane(code="a1", name="NATS", color="a1", epic=None)
        with pytest.raises((AttributeError, TypeError)):
            lane.code = "b"  # type: ignore[misc]

    def test_lane_without_epic(self):
        lane = Lane(code="b", name="Container", color="b", epic=None)
        assert lane.epic is None

    def test_lane_with_epic(self):
        epic = EpicMeta(issue=10, label="Epic", tag="M0")
        lane = Lane(code="a1", name="NATS", color="a1", epic=epic)
        assert lane.epic.issue == 10

    def test_equality_by_value(self):
        lane1 = Lane(code="a1", name="NATS", color="a1", epic=None)
        lane2 = Lane(code="a1", name="NATS", color="a1", epic=None)
        assert lane1 == lane2


# ─── ref_key ─────────────────────────────────────────────────────────────────

class TestRefKey:
    def test_basic_format(self):
        ref = {"repo": "Roxabi/lyra", "issue": 5}
        assert ref_key(ref) == "Roxabi/lyra#5"

    def test_cross_repo(self):
        ref = {"repo": "Roxabi/voiceCLI", "issue": 10}
        assert ref_key(ref) == "Roxabi/voiceCLI#10"

    def test_issue_number_one(self):
        ref = {"repo": "Roxabi/lyra", "issue": 1}
        assert ref_key(ref) == "Roxabi/lyra#1"

    def test_large_issue_number(self):
        ref = {"repo": "Roxabi/lyra", "issue": 9999}
        assert ref_key(ref) == "Roxabi/lyra#9999"


# ─── GraphData ───────────────────────────────────────────────────────────────

class TestGraphData:
    def test_primary_repo(self):
        data = GraphData(
            meta={"repos": ["Roxabi/lyra"]},
            lanes=[],
            lane_by_code={},
            issues={},
            column_groups=list(COLUMN_GROUPS),
            milestones=list(MILESTONES),
        )
        assert data.primary_repo == "Roxabi/lyra"

    def test_defaults(self):
        data = GraphData(
            meta={"repos": ["Roxabi/lyra"]},
            lanes=[],
            lane_by_code={},
            issues={},
            column_groups=list(COLUMN_GROUPS),
            milestones=list(MILESTONES),
        )
        assert data.matrix == {}
        assert data.epic_keys == set()
        assert data.depth_by_key == {}
        assert data.counts == {}
        assert data.total == 0
