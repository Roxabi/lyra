"""Canonical domain model for v5. Loaded once, consumed by both views."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ─── Static configuration (project-level) ───────────────────────────────────

# Column grouping — each tuple is (label, tone_key, [lane_codes]).
COLUMN_GROUPS: list[tuple[str, str, list[str]]] = [
    ("NATS",      "a1", ["a1", "a2", "a3"]),
    ("CONTAINER", "b",  ["b"]),
    ("LLM",       "c1", ["c1", "c2", "c3"]),
    ("OBS",       "d",  ["d"]),
    ("HUB",       "e",  ["e"]),
    ("PLUGINS",   "f",  ["f"]),
    ("VOICE",     "g",  ["g"]),
    ("DEPLOY",    "h",  ["h"]),
    ("VAULT",     "i",  ["i"]),
    ("MEMORY",    "a1", ["j"]),
    ("IDENTITY",  "a2", ["k"]),
    ("TOOLS",     "c1", ["l"]),
    ("OMNI",      "g",  ["m"]),
    ("SOCIAL",    "f",  ["n"]),
    ("FINAL",     "e",  ["o"]),
]

# Milestones — (full label, code, short name).
# NOTE: full label matches GitHub title with em-dashes stripped (double-space).
MILESTONES: list[tuple[str, str, str]] = [
    ("M0  NATS hardening",               "M0",  "NATS hardening"),
    ("M1  NATS maturity  containerize",  "M1",  "NATS maturity / containerize"),
    ("M2  LLM stack modernization",      "M2",  "LLM stack modernization"),
    ("M3  Observability",                "M3",  "Observability"),
    ("M4  Hub statelessness",            "M4",  "Hub statelessness"),
    ("M5  Plugin layer",                 "M5",  "Plugin layer"),
    ("M6  Memory",                       "M6",  "Memory"),
    ("M7  Identity",                     "M7",  "Identity"),
    ("M8  Tools",                        "M8",  "Tools"),
    ("M9  Voice-to-Voice (Omni)",        "M9",  "Voice-to-Voice (Omni)"),
    ("M10  Social Media Bricks",         "M10", "Social Media Bricks"),
    ("Final Initiatives",                "FIN", "Final Initiatives"),
]

MS_CODES: list[str] = [code for _, code, _ in MILESTONES]
MS_NAME_BY_CODE: dict[str, str] = {code: name for _, code, name in MILESTONES}


# ─── Domain dataclasses ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class EpicMeta:
    issue: int | None
    label: str
    tag: str


@dataclass(frozen=True)
class Lane:
    code: str
    name: str
    color: str
    epic: EpicMeta | None


@dataclass
class GraphData:
    """Canonical loaded state. All rendering reads from this."""
    meta: dict[str, Any]
    lanes: list[Lane]
    lane_by_code: dict[str, Lane]
    # Raw issue dicts keyed by "owner/repo#N" — shape matches gh.json.
    issues: dict[str, dict[str, Any]]
    # Cell matrix: (ms_label, lane_code) → [issue dicts], excludes epics.
    matrix: dict[tuple[str, str], list[dict[str, Any]]] = field(default_factory=dict)
    epic_keys: set[str] = field(default_factory=set)
    # Topological depth (counts all blockers, open + closed).
    depth_by_key: dict[str, int] = field(default_factory=dict)
    # Rollup counts after filtering epics.
    counts: dict[str, int] = field(default_factory=dict)
    total: int = 0

    @property
    def primary_repo(self) -> str:
        return self.meta["repos"][0]


def ref_key(ref: dict[str, Any]) -> str:
    """Convert a {repo, issue} ref dict to its canonical 'repo#N' key."""
    return f"{ref['repo']}#{ref['issue']}"
