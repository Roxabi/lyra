"""Title normalization engine for dep-graph cards.

Built-in default rules strip conventional-commit prefixes (feat:, fix:, etc.)
and common noise suffixes.  Per-issue `overrides.<N>.title` wins over rules
(checked before calling normalize_title).

layout.json `title_rules[]` is now optional.  When present, those rules are
applied *in addition to* (and before) the built-in rules.  To suppress the
built-ins entirely, set `title_rules: []` in layout.json (empty list).

Set `title_rules` to `null` or omit the key to use only built-in rules.
"""

import re

# ---------------------------------------------------------------------------
# Built-in rules — applied when layout.json has no title_rules or omits it.
# These mirror what was previously hardcoded in layout.json title_rules[].
# ---------------------------------------------------------------------------

_BUILTIN_RULES: list[dict] = [
    # "feat(scope) [Tag]: title" → "Tag · title"
    {
        "pattern": (
            r"^(feat|fix|chore|docs|refactor|test|ci|perf|style|epic)"
            r"(\([^)]+\))?\s+\[([^\]]+)\]\s*:\s*"
        ),
        "replacement": "$3 \u00b7 ",
    },
    # "feat(scope)[Tag]: title" → "Tag · title"
    {
        "pattern": (
            r"^(feat|fix|chore|docs|refactor|test|ci|perf|style|epic)"
            r"(\([^)]+\))?\[([^\]]+)\]\s*:\s*"
        ),
        "replacement": "$3 \u00b7 ",
    },
    # "feat(scope): title" → "title"
    {
        "pattern": (
            r"^(feat|fix|chore|docs|refactor|test|ci|perf|style|epic)"
            r"(\([^)]+\))?\s*:\s*"
        ),
        "replacement": "",
    },
    # Strip leading "M0 — " / "M1 - " milestone prefixes
    {
        "pattern": r"^M\d+\s*[\u2014\-]\s*",
        "replacement": "",
    },
    # Strip trailing " — subtitle" noise
    {
        "pattern": r"\s*[\u2014\-]\s+.+$",
        "replacement": "",
    },
    # Strip trailing " + subtitle"
    {
        "pattern": r"\s*\+\s+.+$",
        "replacement": "",
    },
]


def normalize_title(raw: str, rules: list[dict] | None) -> str:
    """Apply title normalization rules to a raw GitHub title.

    - If *rules* is None or omitted: uses only the built-in rules.
    - If *rules* is an explicit list (possibly empty): applies those rules
      first, then falls through to built-in rules.  Pass [] to disable
      layout-level rules while still applying built-ins.

    Rules use Python regex; `$N` back-references are converted to `\\N`.
    """
    effective_rules: list[dict]
    if rules is None:
        effective_rules = _BUILTIN_RULES
    else:
        effective_rules = list(rules) + _BUILTIN_RULES

    t = raw
    for rule in effective_rules:
        pattern = rule["pattern"]
        replacement = re.sub(r"\$(\d+)", r"\\\1", rule["replacement"])
        t = re.sub(pattern, replacement, t).strip()
    return t
