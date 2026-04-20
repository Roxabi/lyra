"""Shared fixtures for v5 test suite.

Provides a minimal synthetic dataset (layout + gh dicts) that exercises:
- 5 lanes across different column groups (a1 NATS, b CONTAINER, c1 LLM, d OBS, e HUB)
- 10 issues with varied status / milestone / lane / blocked_by chains
  * chain: issue 1 → 2 → 3 (depth 0, 1, 2)
  * closed issue: issue 4
  * cross-repo ref: issue 5 blocked by Roxabi/voiceCLI#10
  * issues across milestones M0..M4
"""
from __future__ import annotations

import pytest

from v5.data.load import load_from_dicts
from v5.data.model import GraphData

LAYOUT = {
    "meta": {
        "title": "Lyra v2 dep graph",
        "date": "2026-04-20",
        "repos": ["Roxabi/lyra"],
        "issue": {"repo": "Roxabi/lyra", "issue": 99},
    },
    "lanes": [
        {
            "code": "a1",
            "name": "NATS SDK",
            "color": "a1",
            "epic": {"issue": 100, "label": "NATS hardening", "tag": "M0-NATS"},
        },
        {
            "code": "b",
            "name": "Container",
            "color": "b",
            "epic": {"issue": 101, "label": "Containerize", "tag": "M1-CONT"},
        },
        {
            "code": "c1",
            "name": "LLM Stack",
            "color": "c1",
            "epic": {"issue": 102, "label": "LLM stack", "tag": "M2-LLM"},
        },
        {
            "code": "d",
            "name": "Observability",
            "color": "d",
            "epic": {"issue": 103, "label": "OBS", "tag": "M3-OBS"},
        },
        {
            "code": "e",
            "name": "Hub",
            "color": "e",
            "epic": {"issue": 104, "label": "Hub stateless", "tag": "M4-HUB"},
        },
        # Lanes required by COLUMN_GROUPS that have no issues — still need to exist
        {"code": "a2", "name": "NATS auth", "color": "a2"},
        {"code": "a3", "name": "NATS schema", "color": "a3"},
        {"code": "c2", "name": "LLM router", "color": "c2"},
        {"code": "c3", "name": "LLM cache", "color": "c3"},
        {"code": "f", "name": "Plugins", "color": "f"},
        {"code": "g", "name": "Voice", "color": "g"},
        {"code": "h", "name": "Deploy", "color": "h"},
        {"code": "i", "name": "Vault", "color": "i"},
        {"code": "j", "name": "Memory", "color": "a1"},
        {"code": "k", "name": "Identity", "color": "a2"},
        {"code": "l", "name": "Tools", "color": "c1"},
        {"code": "m", "name": "Omni", "color": "g"},
        {"code": "n", "name": "Social", "color": "f"},
        {"code": "o", "name": "Final", "color": "e"},
    ],
}

# gh.json-style dict
GH = {
    "issues": {
        # Epics (should be excluded from matrix / graph)
        "Roxabi/lyra#100": {
            "repo": "Roxabi/lyra", "number": 100, "title": "Epic NATS",
            "state": "open", "milestone": "M0  NATS hardening",
            "lane_label": "a1", "blocked_by": [], "blocking": [],
        },
        "Roxabi/lyra#101": {
            "repo": "Roxabi/lyra", "number": 101, "title": "Epic CONTAINER",
            "state": "open", "milestone": "M1  NATS maturity  containerize",
            "lane_label": "b", "blocked_by": [], "blocking": [],
        },
        "Roxabi/lyra#102": {
            "repo": "Roxabi/lyra", "number": 102, "title": "Epic LLM",
            "state": "open", "milestone": "M2  LLM stack modernization",
            "lane_label": "c1", "blocked_by": [], "blocking": [],
        },
        "Roxabi/lyra#103": {
            "repo": "Roxabi/lyra", "number": 103, "title": "Epic OBS",
            "state": "open", "milestone": "M3  Observability",
            "lane_label": "d", "blocked_by": [], "blocking": [],
        },
        "Roxabi/lyra#104": {
            "repo": "Roxabi/lyra", "number": 104, "title": "Epic HUB",
            "state": "open", "milestone": "M4  Hub statelessness",
            "lane_label": "e", "blocked_by": [], "blocking": [],
        },
        # Real tasks
        # Issue 1 — depth 0 (no blockers), root of chain
        "Roxabi/lyra#1": {
            "repo": "Roxabi/lyra", "number": 1, "title": "Stabilise NATS connection",
            "state": "open", "milestone": "M0  NATS hardening",
            "lane_label": "a1",
            "blocked_by": [],
            "blocking": [{"repo": "Roxabi/lyra", "issue": 2}],
        },
        # Issue 2 — depth 1 (blocked by #1)
        "Roxabi/lyra#2": {
            "repo": "Roxabi/lyra", "number": 2, "title": "NATS circuit breaker",
            "state": "open", "milestone": "M0  NATS hardening",
            "lane_label": "a1",
            "blocked_by": [{"repo": "Roxabi/lyra", "issue": 1}],
            "blocking": [{"repo": "Roxabi/lyra", "issue": 3}],
        },
        # Issue 3 — depth 2 (blocked by #2 blocked by #1)
        "Roxabi/lyra#3": {
            "repo": "Roxabi/lyra", "number": 3, "title": "NATS readiness probe",
            "state": "open", "milestone": "M0  NATS hardening",
            "lane_label": "a1",
            "blocked_by": [{"repo": "Roxabi/lyra", "issue": 2}],
            "blocking": [],
        },
        # Issue 4 — closed (done)
        "Roxabi/lyra#4": {
            "repo": "Roxabi/lyra", "number": 4, "title": "Docker base image",
            "state": "closed", "milestone": "M1  NATS maturity  containerize",
            "lane_label": "b",
            "blocked_by": [],
            "blocking": [],
        },
        # Issue 5 — blocked by cross-repo voiceCLI#10 (open)
        "Roxabi/lyra#5": {
            "repo": "Roxabi/lyra", "number": 5, "title": "Voice bridge setup",
            "state": "open", "milestone": "M2  LLM stack modernization",
            "lane_label": "c1",
            "blocked_by": [{"repo": "Roxabi/voiceCLI", "issue": 10}],
            "blocking": [],
        },
        # voiceCLI issue used as blocker reference (may be absent from issues)
        "Roxabi/voiceCLI#10": {
            "repo": "Roxabi/voiceCLI", "number": 10, "title": "Voice TTS stream",
            "state": "open", "milestone": None, "lane_label": None,
            "blocked_by": [], "blocking": [],
        },
        # Issue 6 — ready (open, no open blockers)
        "Roxabi/lyra#6": {
            "repo": "Roxabi/lyra", "number": 6, "title": "Span metrics",
            "state": "open", "milestone": "M3  Observability",
            "lane_label": "d",
            "blocked_by": [],
            "blocking": [],
        },
        # Issue 7 — blocked by closed issue → should be ready
        "Roxabi/lyra#7": {
            "repo": "Roxabi/lyra", "number": 7, "title": "Hub state flush",
            "state": "open", "milestone": "M4  Hub statelessness",
            "lane_label": "e",
            "blocked_by": [{"repo": "Roxabi/lyra", "issue": 4}],
            "blocking": [],
        },
        # Issue 8 — size label
        "Roxabi/lyra#8": {
            "repo": "Roxabi/lyra", "number": 8, "title": "Plug-in API <draft>",
            "state": "open", "milestone": "M0  NATS hardening",
            "lane_label": "a1", "size": "L",
            "blocked_by": [],
            "blocking": [],
        },
    }
}


@pytest.fixture
def layout() -> dict:
    return LAYOUT


@pytest.fixture
def gh() -> dict:
    return GH


@pytest.fixture
def graph_data(layout, gh) -> GraphData:
    return load_from_dicts(layout, gh)
