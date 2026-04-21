"""Title normalization engine for dep-graph cards.

Built-in default rules strip conventional-commit prefixes (feat:, fix:, etc.)
and common noise suffixes.  Per-issue `overrides.<N>.title` wins over rules
(checked before calling normalize_title).

layout.json `title_rules[]` is now optional.  When present, those rules are
applied *in addition to* (and before) the built-in rules.  Set `title_rules`
to `null` or omit the key to use only built-in rules; setting it to `[]`
behaves the same as omitting it (no additional rules, built-ins still apply).
The built-in rules cannot be suppressed — if a layout rule overlaps with a
built-in, the built-in is a safe idempotent pass-through.
"""

import re
import sys

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

_BACKREF_RE = re.compile(r"\$(\d+)")


def _compile_rules(rules: list[dict]) -> list[tuple[re.Pattern[str], str]]:
    """Pre-compile (pattern, replacement) pairs.

    Rules that fail to compile are skipped with a warning so a single bad
    user rule doesn't sink the whole pipeline.
    """
    compiled: list[tuple[re.Pattern[str], str]] = []
    for rule in rules:
        raw_pattern = rule["pattern"]
        replacement = _BACKREF_RE.sub(r"\\\1", rule["replacement"])
        try:
            compiled.append((re.compile(raw_pattern), replacement))
        except re.error as exc:
            print(
                f"  WARN title_rule regex error: {exc} (pattern={raw_pattern!r})",
                file=sys.stderr,
            )
    return compiled


_COMPILED_BUILTINS = _compile_rules(_BUILTIN_RULES)


def normalize_title(raw: str, rules: list[dict] | None) -> str:
    """Apply title normalization rules to a raw GitHub title.

    - If *rules* is None or omitted: uses only the built-in rules.
    - If *rules* is an explicit list (possibly empty): applies those rules
      first, then falls through to built-in rules.  Pass [] to apply no
      additional layout-level rules (built-ins still run).

    Rules use Python regex; `$N` back-references are converted to `\\N`.
    """
    compiled = (
        _compile_rules(rules) + _COMPILED_BUILTINS if rules else _COMPILED_BUILTINS
    )
    t = raw
    for pattern, replacement in compiled:
        t = pattern.sub(replacement, t).strip()
    return t
