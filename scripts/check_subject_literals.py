#!/usr/bin/env python3
"""check_subject_literals.py — every raw lyra.* subject literal in src/ resolves.

Scans src/ for raw ``lyra.*`` NATS subject string literals and verifies each one
against the semantic CodeInventory oracle (tools/code_inventory.py), whose
``subjects`` set is sourced from deploy/nats/acl-matrix.json + roxabi-contracts
SUBJECTS. A literal that the oracle classifies as a subject but cannot resolve
(``kind == "subject" and not exists``) is an *orphan*: a subject used in code but
declared nowhere. Orphans fail CI — unless baselined in the allowlist.

Re-scoped per ADR-081 (#1530): resolution goes through ``oracle.resolve(token)``,
NOT a bespoke acl-matrix/contracts resolver. The oracle owns subject knowledge.

Module/logger names (``lyra.adapters.telegram``) resolve as ``kind == "module"``
and are never flagged. Three classes of subject-shaped non-subjects are filtered
out before resolution to avoid false positives:
  - f-string fragments  (``f"lyra.outbound.{bot}"`` → fragment ``"lyra.outbound."``)
  - logging.getLogger() arguments (logger names, e.g. ``"lyra.security"``)
  - filenames           (final segment is a file extension, e.g. ``"lyra.toml"``)

Exit codes (mirror the sibling src/ scanners, e.g. check_inbox_prefix.py):
  0 — scan ran, no orphan subjects found
  1 — orphan subject(s) found, OR scanner failure (bad --src path, I/O error)
  2 — oracle scanned source with SyntaxError(s) (the inventory is unreliable)
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

# Ensure repo root is on sys.path when executed directly as a script.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from tools.code_inventory import CodeInventory
except ModuleNotFoundError:  # pragma: no cover - standalone import fallback
    _tools_dir = _REPO_ROOT / "tools"
    if str(_tools_dir) not in sys.path:
        sys.path.insert(0, str(_tools_dir))
    from code_inventory import CodeInventory  # type: ignore[no-redef]

_DEFAULT_ALLOWLIST = _REPO_ROOT / "scripts" / "subject_literals_allowlist.txt"

# Final dotted segments that mark a filename, not a NATS subject (lyra.toml, …).
_FILE_EXTENSIONS = frozenset(
    {
        "toml",
        "json",
        "yaml",
        "yml",
        "ini",
        "cfg",
        "conf",
        "env",
        "lock",
        "py",
        "pyi",
        "sh",
        "sql",
        "db",
        "log",
        "txt",
        "md",
        "csv",
        "html",
        "css",
        "js",
        "ts",
    }
)


# ---------------------------------------------------------------------------
# Source discovery
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Literal extraction (AST — ignores comments and docstrings by construction)
# ---------------------------------------------------------------------------


def _fstring_fragment_ids(tree: ast.AST) -> set[int]:
    """id()s of Constant nodes that are static fragments of an f-string.

    ``f"lyra.outbound.{bot}"`` yields the fragment Constant ``"lyra.outbound."`` —
    an incomplete subject that must not be resolved as a standalone literal.
    """
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            for value in node.values:
                if isinstance(value, ast.Constant):
                    ids.add(id(value))
    return ids


def _getlogger_arg_ids(tree: ast.AST) -> set[int]:
    """id()s of Constant nodes passed as the first arg to ``getLogger(...)``.

    Logger names share the dotted ``lyra.*`` shape but are never NATS subjects.
    """
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            func.attr
            if isinstance(func, ast.Attribute)
            else func.id
            if isinstance(func, ast.Name)
            else None
        )
        if name == "getLogger" and node.args:
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                ids.add(id(first))
    return ids


def _looks_like_filename(literal: str) -> bool:
    """True if the final dotted segment is a known file extension (lyra.toml)."""
    return literal.rsplit(".", 1)[-1].lower() in _FILE_EXTENSIONS


def _extract_subject_literals(
    path: Path,
    parse_errors: list[tuple[Path, str]],
) -> dict[str, list[int]]:
    """Map each candidate ``lyra.*`` literal in *path* to its line numbers.

    Excludes f-string fragments, getLogger() arguments, and filenames.
    On SyntaxError/UnicodeDecodeError/OSError, appends ``(path, reason)`` to
    *parse_errors* and returns ``{}`` so the caller can surface the failure.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError as exc:
        parse_errors.append((path, f"SyntaxError: {exc}"))
        return {}
    except UnicodeDecodeError as exc:
        parse_errors.append((path, f"UnicodeDecodeError: {exc}"))
        return {}
    except OSError as exc:
        parse_errors.append((path, f"OSError: {exc}"))
        return {}

    skip = _fstring_fragment_ids(tree) | _getlogger_arg_ids(tree)
    found: dict[str, list[int]] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if id(node) in skip:
            continue
        value = node.value
        if not value.lower().startswith("lyra."):
            continue
        if " " in value or _looks_like_filename(value):
            continue
        found.setdefault(value, []).append(node.lineno)
    return found


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------


def _load_allowlist(path: Path) -> set[str]:
    """Load baselined orphan subjects. Missing file → empty allowlist."""
    if not path.exists():
        return set()
    entries: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.split("#", 1)[0].strip()
        if stripped:
            entries.add(stripped)
    return entries


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------


def _scan(
    inventory: CodeInventory,
    files: list[Path],
    allowlist: set[str],
    parse_errors: list[tuple[Path, str]],
) -> dict[str, list[str]]:
    """Return ``{orphan_subject: [<file>:<line>, ...]}`` over *files*.

    Files that cannot be parsed are recorded in *parse_errors*.
    """
    orphans: dict[str, list[str]] = {}
    for path in files:
        for literal, linenos in _extract_subject_literals(path, parse_errors).items():
            if literal in allowlist:
                continue
            verdict = inventory.resolve(literal)
            if verdict.kind == "subject" and not verdict.exists:
                orphans.setdefault(literal, []).extend(
                    f"{path}:{lineno}" for lineno in linenos
                )
    return orphans


def _resolve_src_dirs(src_dirs: list[Path] | None) -> list[Path]:
    """Return the effective source directories to scan."""
    if src_dirs:
        return src_dirs
    return [d for d in [_REPO_ROOT / "src"] if d.is_dir()]


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Check that every raw lyra.* subject literal in src/ resolves to the "
            "CodeInventory oracle (acl-matrix.json + roxabi-contracts SUBJECTS)."
        )
    )
    parser.add_argument(
        "--src",
        type=Path,
        action="append",
        dest="src_dirs",
        metavar="DIR",
        help="Source directory to scan (repeatable). Defaults to src/.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=_REPO_ROOT,
        help="Repo root the oracle builds its subject inventory from.",
    )
    parser.add_argument(
        "--allowlist",
        type=Path,
        default=_DEFAULT_ALLOWLIST,
        help="File of baselined orphan subjects to ignore.",
    )
    args = parser.parse_args()

    src_dirs = _resolve_src_dirs(args.src_dirs)
    for d in src_dirs:
        if not d.is_dir():
            print(f"ERROR: source directory not found: {d}", file=sys.stderr)
            sys.exit(1)

    inventory = CodeInventory.build(args.root)
    if inventory.syntax_errors:
        print(
            f"ERROR: oracle hit {len(inventory.syntax_errors)} syntax error(s) while "
            "scanning source — subject inventory is unreliable:",
            file=sys.stderr,
        )
        for path, msg in inventory.syntax_errors:
            print(f"  {path}: {msg}", file=sys.stderr)
        sys.exit(2)

    allowlist = _load_allowlist(args.allowlist)
    parse_errors: list[tuple[Path, str]] = []
    orphans = _scan(inventory, _iter_source_files(src_dirs), allowlist, parse_errors)

    if parse_errors:
        print(
            f"ERROR: scanner failed to parse {len(parse_errors)} file(s) — "
            "subject inventory for those files is incomplete:",
            file=sys.stderr,
        )
        for path, reason in parse_errors:
            print(f"  {path}: {reason}", file=sys.stderr)
        sys.exit(2)

    if orphans:
        print(
            f"FAIL: {len(orphans)} orphan subject literal(s) in src/ "
            "not declared in acl-matrix.json or roxabi-contracts SUBJECTS:"
        )
        for subject in sorted(orphans):
            print(f"  {subject}")
            for location in orphans[subject]:
                print(f"    {location}")
        print(
            "\nDeclare each subject (acl-matrix publish/subscribe grant or contracts "
            "SUBJECTS), or baseline it in scripts/subject_literals_allowlist.txt."
        )
        sys.exit(1)

    print("check-subject-literals: OK (all lyra.* literals in src/ resolve)")


if __name__ == "__main__":
    main()
