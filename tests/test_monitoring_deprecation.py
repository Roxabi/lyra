"""Tests for factory.monitoring deprecation-warning routing (issue #2245, T13).

Verifies the T12 outcome: the DeprecationWarning that flags the deprecated
Layer-1 aggregate runner lives ONLY on `factory.monitoring.checks` (and
anything that imports it, e.g. `factory.monitoring.__main__`) — NOT on the
live V1-pull modules (`checks_log`, `escalation`) that survive into the thin
V1-pull slice.

Caching gotcha (verified empirically, see PR/task notes): this repo's pytest
config runs under xdist (`-n auto --dist=loadgroup`), and several *other*
test files (`tests/test_monitoring_checks_http_idle_reaper.py`,
`tests/test_monitoring_escalation.py`) import `factory.monitoring.checks` /
`factory.monitoring.__main__` themselves. `factory.monitoring.checks` sorts
before `factory.monitoring.deprecation` alphabetically, so on any xdist
worker that happens to collect both files, `factory.monitoring.checks` is
ALREADY in `sys.modules` by the time this file's tests run. A plain `import`
statement against an already-cached module is a no-op — it does NOT
re-execute the module-level `warnings.warn(...)` call. Relying on plain
`import` here would make `test_dormant_modules_still_warn` pass or fail
depending on xdist worker assignment (flaky-by-scheduling), not on the
behavior under test.

Fix: force a fresh execution via `sys.modules.pop(...)` +
`importlib.import_module(...)` inside each `catch_warnings` block, so the
assertions are evidence about the source, not an artifact of import order.
`test_live_v1_pull_modules_no_deprecation_warning` doesn't strictly need
this (those modules never warn, cached or not) but uses the same helper for
consistency and because the plan explicitly asks for reload-safety here.
"""

from __future__ import annotations

import importlib
import re
import sys
import tomllib
import warnings
from pathlib import Path

_PYPROJECT_PATH = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _fresh_import(
    module_name: str, *, also_evict: tuple[str, ...] = ()
) -> list[warnings.WarningMessage]:
    """Import `module_name` from scratch, forcing re-execution of its
    module-level code even if it (or a module it transitively imports) is
    already cached in `sys.modules` from an earlier test's import.

    Returns the list of warnings recorded during the (re-)import.
    """
    for name in (*also_evict, module_name):
        sys.modules.pop(name, None)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        importlib.import_module(module_name)
    return caught


def test_live_v1_pull_modules_no_deprecation_warning():
    """checks_log.py and escalation.py never import .checks — importing (or
    re-importing) either must not emit the Layer-1-aggregate
    DeprecationWarning (or any DeprecationWarning at all)."""
    caught = _fresh_import("factory.monitoring.checks_log")
    caught += _fresh_import("factory.monitoring.escalation")

    deprecation_warnings = [
        w for w in caught if issubclass(w.category, DeprecationWarning)
    ]
    assert not deprecation_warnings, (
        "checks_log/escalation must not emit any DeprecationWarning on "
        f"import, got: {[str(w.message) for w in deprecation_warnings]}"
    )


def test_dormant_modules_still_warn():
    """factory.monitoring.checks (module-level warnings.warn) and
    factory.monitoring.__main__ (transitively, via `from .checks import
    run_checks`) must still emit the deprecation warning.

    Self-validates the cache-eviction premise from the module docstring: we
    first import `factory.monitoring.checks` ONCE, outside any
    catch_warnings block, to seed `sys.modules` the way an earlier test file
    would on a shared xdist worker. If `_fresh_import`'s
    `sys.modules.pop(...)` did nothing, the subsequent assertions would find
    zero recorded warnings (plain `import` against a cached module is a
    no-op) — so a pass here is proof the eviction is load-bearing, not
    coincidental.
    """
    importlib.import_module("factory.monitoring.checks")  # seed the cache
    assert "factory.monitoring.checks" in sys.modules

    checks_caught = _fresh_import("factory.monitoring.checks")
    checks_deprecations = [
        w for w in checks_caught if issubclass(w.category, DeprecationWarning)
    ]
    assert checks_deprecations, (
        "factory.monitoring.checks did not warn on import (was the cached "
        "module reused instead of re-executed?)"
    )
    assert any(
        "factory.monitoring.checks" in str(w.message) and "deprecated" in str(w.message)
        for w in checks_deprecations
    ), [str(w.message) for w in checks_deprecations]

    # Evict BOTH __main__ and .checks so re-importing __main__ forces a fresh
    # execution of its `from .checks import run_checks` line — otherwise the
    # cached .checks module (just re-imported above) would satisfy that
    # import without re-running checks.py's warnings.warn call, producing a
    # false pass that doesn't actually prove __main__ transitively warns.
    main_caught = _fresh_import(
        "factory.monitoring.__main__", also_evict=("factory.monitoring.checks",)
    )
    main_deprecations = [
        w for w in main_caught if issubclass(w.category, DeprecationWarning)
    ]
    assert main_deprecations, (
        "factory.monitoring.__main__ did not transitively warn on import "
        "(expected `from .checks import run_checks` to re-trigger checks.py's "
        "module-level warnings.warn)"
    )
    assert any(
        "factory.monitoring.checks" in str(w.message) and "deprecated" in str(w.message)
        for w in main_deprecations
    ), [str(w.message) for w in main_deprecations]


def test_pyproject_filterwarnings_regex_matches_actual_message():
    """Sanity cross-check: pyproject.toml's `[tool.pytest.ini_options]
    filterwarnings` carries an `ignore:factory.monitoring.checks:...` entry
    meant to suppress this exact warning suite-wide. pytest's filterwarnings
    strings use `action:message:category` where `message` is matched via
    `re.compile(message, re.I).match(...)` against the warning's message —
    i.e. a case-insensitive PREFIX match, not substring/full-string.

    This test reads the actual filter string out of pyproject.toml (rather
    than hardcoding a copy of the regex) so it breaks if someone edits or
    removes that entry, and proves the pattern it declares really matches
    the real message text `checks.py` emits — so every OTHER test file that
    happens to import checks.py/__main__.py incidentally is correctly
    shielded from this warning turning into a `filterwarnings = ["error"]`
    -style failure.
    """
    with _PYPROJECT_PATH.open("rb") as fh:
        pyproject = tomllib.load(fh)

    filters: list[str] = pyproject["tool"]["pytest"]["ini_options"]["filterwarnings"]
    matching = [f for f in filters if "factory.monitoring.checks" in f]
    assert matching, (
        "expected a pyproject.toml filterwarnings entry mentioning "
        "'factory.monitoring.checks' (temporary suppression for #1035); "
        "found none — either it was removed, or checks.py's deprecation "
        "warning is no longer suppressed suite-wide"
    )

    # filterwarnings entries are "action:message:category:module:lineno";
    # only the first three fields are populated here.
    action, message_pattern, category = matching[0].split(":")[:3]
    assert action == "ignore"
    assert category == "DeprecationWarning"

    caught = _fresh_import("factory.monitoring.checks")
    deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert deprecations, "expected a DeprecationWarning from importing checks.py"

    actual_message = str(deprecations[0].message)
    assert re.compile(message_pattern, re.IGNORECASE).match(actual_message), (
        f"pyproject.toml filterwarnings pattern {message_pattern!r} no longer "
        f"matches the actual warning message: {actual_message!r}"
    )
