"""Canonical domain model for v5. Loaded once, consumed by both views."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ─── Static configuration (project-level) ───────────────────────────────────

# Column grouping — each tuple is (label, tone_key, [lane_codes]).
# Tuple positions are load-bearing: parse_column_groups and every consumer
# unpacks by position; do not reorder without updating all call sites.
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

# Milestones — (full_label, code, short_display).
# Tuple positions are load-bearing: parse_milestones and every consumer
# unpacks by position; do not reorder without updating all call sites.
# NOTE: full_label matches GitHub title with em-dashes stripped (double-space).
#       short_display is the row-header label used by the grid view (indexed by ms_name_by_code).
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

# Sentinel keys for issues in the visibility set that lack milestone/lane.
# Rendered as a prepended row ("No milestone") or column ("No lane") when
# non-empty; hidden otherwise.
NO_MS: str = "__nomilestone__"
NO_LANE: str = "__nolane__"


# ─── Layout-driven config parsers ───────────────────────────────────────────

def parse_column_groups(
    raw: list[dict[str, Any]],
) -> list[tuple[str, str, list[str]]]:
    """Parse layout.json column_groups[] → internal tuple form."""
    return [
        (item["label"], item["tone"], list(item["lane_codes"]))
        for item in raw
    ]


def parse_milestones(
    raw: list[dict[str, Any]],
) -> list[tuple[str, str, str]]:
    """Parse layout.json milestones[] → internal tuple form."""
    return [
        (item["label"], item["code"], item["short"])
        for item in raw
    ]


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
    # Effective matrix config — overridable via layout.json, else module defaults.
    column_groups: list[tuple[str, str, list[str]]] = field(
        default_factory=lambda: list(COLUMN_GROUPS)
    )
    milestones: list[tuple[str, str, str]] = field(
        default_factory=lambda: list(MILESTONES)
    )
    # Cell matrix: (ms_label, lane_code) → [issue dicts], excludes epics.
    matrix: dict[tuple[str, str], list[dict[str, Any]]] = field(default_factory=dict)
    epic_keys: set[str] = field(default_factory=set)
    # Visibility set — canonical keys the grid/graph should render. Rule:
    #   · all open items in primary repo
    #   · forward cascade (blocking edges), any state, any repo
    #   · 1-hop backward (blocked_by), any state, any repo
    visible: set[str] = field(default_factory=set)
    # Topological depth (counts all blockers, open + closed).
    depth_by_key: dict[str, int] = field(default_factory=dict)
    # Rollup counts after filtering epics.
    counts: dict[str, int] = field(default_factory=dict)
    total: int = 0

    @property
    def primary_repo(self) -> str:
        return self.meta["repos"][0]

    @property
    def ms_codes(self) -> list[str]:
        return [code for _, code, _ in self.milestones]

    @property
    def ms_name_by_code(self) -> dict[str, str]:
        """Map milestone code → short display label (position 2 of the tuple).

        Name kept for backward-compat with the module-level `MS_NAME_BY_CODE`
        constant; despite "name", the value is the short display string used
        by row headers, not the full milestone label.
        """
        return {code: name for _, code, name in self.milestones}

    @property
    def lane_order(self) -> list[str]:
        """Flat lane-code order derived from column_groups (tie-break for graph layout)."""
        return [code for _, _, codes in self.column_groups for code in codes]


def ref_key(ref: dict[str, Any]) -> str:
    """Convert a {repo, issue} ref dict to its canonical 'repo#N' key."""
    return f"{ref['repo']}#{ref['issue']}"
