"""Title normalization engine for dep-graph cards.

Rules are applied in order from layout.json `title_rules[]`.
Each rule is { "pattern": "<regex>", "replacement": "<str>" }.
Per-issue `overrides.<N>.title` wins over rules (checked before calling
normalize_title).
"""

import re


def normalize_title(raw: str, rules: list[dict]) -> str:
    """Apply title_rules[] sequentially to raw GitHub title.

    Rules use Python regex; `$1` in replacement is converted to `\\1`.
    """
    t = raw
    for rule in rules:
        pattern = rule["pattern"]
        # Convert $N back-references to \\N for re.sub
        replacement = re.sub(r"\$(\d+)", r"\\\1", rule["replacement"])
        t = re.sub(pattern, replacement, t).strip()
    return t
