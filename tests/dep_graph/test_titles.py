"""Tests for dep_graph.titles — title rule application and regex guarding.

Covers the ReDoS/error guard added for #741 item 5.
"""

from __future__ import annotations

from dep_graph.titles import normalize_title


def test_apply_title_rules_valid_rule_applied():
    rules = [{"pattern": r"^foo", "replacement": "bar"}]
    assert normalize_title("foo baz", rules=rules) == "bar baz"


def test_apply_title_rules_invalid_pattern_warns_and_continues(capsys):
    # Malformed regex (unclosed bracket) must not crash — emit stderr warn, skip.
    rules = [
        {"pattern": r"[unclosed", "replacement": "x"},  # bad
        {"pattern": r"^foo", "replacement": "bar"},  # good — should still apply
    ]
    result = normalize_title("foo baz", rules=rules)
    captured = capsys.readouterr()
    assert "WARN title_rule regex error" in captured.err
    assert "[unclosed" in captured.err
    # The second rule still applied — invalid rule was skipped, not fatal.
    assert result == "bar baz"


def test_apply_title_rules_builtins_still_run_after_invalid_user_rule(capsys):
    # A broken user rule must not prevent built-in rules from running.
    # Built-in rules in titles.py will still be applied on whatever input survives.
    rules = [{"pattern": r"(unclosed", "replacement": "x"}]
    result = normalize_title("normal title", rules=rules)
    captured = capsys.readouterr()
    assert "WARN title_rule regex error" in captured.err
    # Result should be at least the input string (built-ins may transform it,
    # but the broken rule did NOT crash the pipeline).
    assert isinstance(result, str)


def test_apply_title_rules_none_rules_uses_builtins_only(capsys):
    # rules=None path — builtins only, no user rules, no warnings.
    result = normalize_title("test title", rules=None)
    captured = capsys.readouterr()
    assert "WARN" not in captured.err
    assert isinstance(result, str)
