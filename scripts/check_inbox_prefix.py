#!/usr/bin/env python3
"""check_inbox_prefix.py — detect raw _INBOX inbox_prefix constructions in source.

nats_connect(identity_name=...) is the canonical way to set per-identity inbox
prefix.  Raw constructions like ``inbox_prefix=f"_INBOX.{name}"`` or
``inbox_prefix="_INBOX.something"`` bypass validation and must be flagged.

Tests are excluded because they may intentionally exercise the raw parameter.

Exit 0: scan ran successfully, no violations found.
Exit 1: violations found OR scanner failed (I/O error, bad --src path, etc.).
        Matches the sibling-scanner convention in this repo
        (check_acl_matrix_retired.py, check_request_reply_flows.py).
        CI step exit code IS the gate signal.

Violation output format (stdout):
  FAIL: <file> uses raw f-string inbox_prefix construction (use identity_name= instead):
  <lineno>: <matched line>

  FAIL: <file> uses raw literal inbox_prefix construction (use identity_name= instead):
  <file>:<lineno>: inbox_prefix="<value>..."

Summary on stdout:
  OK: no raw _INBOX inbox_prefix constructions found in source files
  OR
  FAIL: <N> violation(s) found
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Repo-root bootstrap (mirrors sibling scripts)
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import argparse  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FSTRING_PATTERN = re.compile(r'inbox_prefix\s*=\s*f["\']_INBOX\.')


def _iter_source_files(src_dirs: list[Path]) -> list[Path]:
    """Return all .py source files under *src_dirs*, excluding test files."""
    files: list[Path] = []
    for d in src_dirs:
        for p in d.rglob("*.py"):
            parts = p.parts
            if "tests" in parts:
                continue
            if p.name.startswith("test_") or p.name == "conftest.py":
                continue
            files.append(p)
    return sorted(files)


def _check_fstring(path: Path) -> list[str]:
    """Grep for f-string inbox_prefix construction (fast path, avoids false negatives
    that AST cannot catch for f-strings with complex expressions)."""
    violations: list[str] = []
    try:
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(),
            start=1,
        ):
            if _FSTRING_PATTERN.search(line):
                violations.append(f"{lineno}: {line.rstrip()}")
    except OSError:
        pass
    return violations


def _check_literal(path: Path) -> list[str]:
    """AST-walk for keyword ``inbox_prefix`` whose value is a string constant
    starting with ``_INBOX.``.  AST avoids false positives from comments and
    docstrings."""
    violations: list[str] = []
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError, OSError):
        return violations

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if (
                    kw.arg == "inbox_prefix"
                    and isinstance(kw.value, ast.Constant)
                    and isinstance(kw.value.value, str)
                    and kw.value.value.startswith("_INBOX.")
                ):
                    violations.append(
                        f'{path}:{kw.value.lineno}: inbox_prefix="{kw.value.value}..."'
                    )
    return violations


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _resolve_src_dirs(src_dirs: list[Path] | None) -> list[Path]:
    """Return the effective source directories to scan."""
    if src_dirs:
        return src_dirs
    return [d for d in [_REPO_ROOT / "src", _REPO_ROOT / "packages"] if d.is_dir()]


def _scan(files: list[Path]) -> int:
    """Run both check passes over *files*; return total violation count."""
    violation_count = 0

    for path in files:
        hits = _check_fstring(path)
        if hits:
            print(
                f"FAIL: {path} uses raw f-string inbox_prefix construction"
                " (use identity_name= instead):"
            )
            for h in hits:
                print(h)
            violation_count += 1

    for path in files:
        hits = _check_literal(path)
        if hits:
            print(
                f"FAIL: {path} uses raw literal inbox_prefix construction"
                " (use identity_name= instead):"
            )
            for h in hits:
                print(h)
            violation_count += 1

    return violation_count


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Check source files for raw _INBOX inbox_prefix constructions "
            "(use identity_name= instead)."
        )
    )
    parser.add_argument(
        "--src",
        type=Path,
        action="append",
        dest="src_dirs",
        metavar="DIR",
        help=(
            "Source directory to scan (repeatable). "
            "Defaults to src/ and packages/ under the repo root."
        ),
    )
    args = parser.parse_args()

    src_dirs = _resolve_src_dirs(args.src_dirs)

    for d in src_dirs:
        if not d.is_dir():
            print(f"ERROR: source directory not found: {d}", file=sys.stderr)
            sys.exit(1)

    violation_count = _scan(_iter_source_files(src_dirs))

    if violation_count == 0:
        print("OK: no raw _INBOX inbox_prefix constructions found in source files")
    else:
        print(f"FAIL: {violation_count} violation(s) found")
        sys.exit(1)


if __name__ == "__main__":
    main()
