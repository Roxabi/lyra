"""Regression guard: v1 RenderEvent symbols must not be re-imported or re-defined.

Enforces SC10 of #1214: after the v2 cutover (#1192 slice 3) and the recap-card
rebuild (#1214), no module may import or re-define ``ToolSummaryRenderEvent`` or
``tool_recap_format``. Catches accidental resurrection during refactors.

Historical references in comments, docstrings, recorded fixtures, and parity
shims are intentionally allowed — they describe what was removed and why. The
forbidden patterns target *live* usage:
  - ``import`` / ``from ... import`` statements that would re-introduce a runtime dep
  - The string ``tool_recap_format.`` (attribute access on the deleted module)
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SEARCH_ROOTS = ("src", "tests", "packages")
SELF_PATH = Path(__file__).relative_to(REPO_ROOT).as_posix()

# (label, regex) pairs. Each regex must match a *live* usage, not a history reference.
_RECAP_IMPORT = r"^\s*(from\s+\S*tool_recap_format|import\s+\S*tool_recap_format)"
_TOOL_SUMMARY_IMPORT = (
    r"^\s*from\s+\S+\s+import\s+(?:[^,#\n]*,\s*)*ToolSummaryRenderEvent\b"
)
FORBIDDEN_PATTERNS: tuple[tuple[str, str], ...] = (
    ("import tool_recap_format", _RECAP_IMPORT),
    ("attr access tool_recap_format.X", r"\btool_recap_format\."),
    ("import ToolSummaryRenderEvent", _TOOL_SUMMARY_IMPORT),
)


@pytest.mark.parametrize(("label", "pattern"), FORBIDDEN_PATTERNS)
def test_v1_symbol_absent_from_source_tree(label: str, pattern: str) -> None:
    result = subprocess.run(
        ["git", "grep", "-n", "-E", pattern, "--", *SEARCH_ROOTS],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    matches = [
        line
        for line in result.stdout.splitlines()
        if line and not line.startswith(SELF_PATH + ":")
    ]
    # Defence in depth: also drop lines that are inside a comment (#) AFTER stripping
    # the leading "path:lineno:" prefix produced by `git grep -n`. A pattern hit on a
    # line whose code body starts with `#` is a comment, not real usage.
    code_matches = [m for m in matches if not _is_comment_match(m)]
    assert not code_matches, (
        f"Forbidden v1 usage ({label!r}, pattern {pattern!r}) found in:\n  "
        + "\n  ".join(code_matches)
        + "\nRemove the live reference or update this guard if the policy has changed."
    )


def _is_comment_match(grep_line: str) -> bool:
    # `git grep -n` emits "<path>:<lineno>:<code>"
    parts = grep_line.split(":", 2)
    if len(parts) < 3:
        return False
    code = parts[2].lstrip()
    return code.startswith("#")


# Sanity test — make sure the pattern would catch a real regression if introduced.
# Embeds the literal so it doesn't itself re-import the symbol.
def test_pattern_would_catch_real_import() -> None:
    sample = "from factory.core.messaging.render_events import ToolSummaryRenderEvent"
    pattern = FORBIDDEN_PATTERNS[2][1]
    assert re.search(pattern, sample), (
        "Import-pattern guard does not catch real import statements."
    )
