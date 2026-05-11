#!/usr/bin/env python3
"""Classify UNTAGGED quality-debt rows from audit report.

Usage:
    python tools/classify_quality_debt.py [--report PATH] [--dry-run|--apply] [--json]

Default report: artifacts/quality-debt-report.json (resolved from cwd).
Default mode: --dry-run.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

Row = dict[str, Any]

CLASSIFIED = "classified"
NEEDS_REVIEW = "needs_review"

_REGISTRY_TEMPLATE = """\
---
id: {slug}
slug: {slug}
title: {title}
status: open
created: {today}
drain_slice: P2a
parent_slice: '#1162'
rule: {rule}
rules:
  - {rule}
sites: see artifacts/quality-debt-report.json
fix_class: needs_review
---

# {title}

## Pattern

<!-- TODO: describe what the pattern is and why it's debt vs POLICY -->

## Sites

See artifacts/quality-debt-report.json for live site list.

## Drain plan

<!-- TODO: concrete refactor recipe -->

## Notes

<!-- TODO: history, related work, why not POLICY, lifecycle remarks -->
"""

_INDEX_HEADER = (
    "# Quality-Debt Registry — INDEX\n\n"
    "| Slug | Status | Rules | Sites | Drain slice | Created |\n"
    "|------|--------|-------|-------|-------------|------|\n"
    "<!-- rows inserted here -->\n"
)

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)^---\s*\n", re.DOTALL | re.MULTILINE)
_FM_FIELD_RE = re.compile(r"^(\w+):\s*(.+)$", re.MULTILINE)
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

# ---------------------------------------------------------------------------
# Heuristics
# ---------------------------------------------------------------------------


def _safe_repo_path(root: Path, path_str: str) -> Path | None:
    """Return resolved path iff contained in root. Otherwise None (caller skips)."""
    try:
        candidate = (root / path_str).resolve()
        if candidate.is_relative_to(root.resolve()):
            return candidate
    except (OSError, ValueError):
        pass
    return None


def _read_line(root: Path, path_str: str, lineno: int) -> str:
    """Read a specific line (1-based) from a source file; return \'\'\' on error."""
    p = _safe_repo_path(root, path_str)
    if p is None:
        print(f"skipped path outside root: {path_str}", file=sys.stderr)
        return ""
    if not p.exists():
        return ""
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        if 1 <= lineno <= len(lines):
            return lines[lineno - 1]
    except OSError:
        pass
    return ""


def _read_lines(root: Path, path_str: str) -> list[str]:
    """Read all lines from a source file."""
    p = _safe_repo_path(root, path_str)
    if p is None:
        print(f"skipped path outside root: {path_str}", file=sys.stderr)
        return []
    if not p.exists():
        return []
    try:
        return p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def _classify_row(row: Row, root: Path) -> tuple[str, str | None, str, str]:
    """Return (status, suggestion, fix_class, reason).

    status: \'classified\' | \'needs_review\'
    suggestion: e.g. \'POLICY:boundary\' or None
    fix_class: \'easy\' | \'medium\' | \'needs_review\'
    reason: \'matched\' | \'f401-init\' | \'not-implemented\'
    """
    rule: str = row.get("rule", "")
    path: str = row.get("path", "")
    lineno: int = int(row.get("line", 1))

    # BLE001 — CLI top-level boundary
    if rule == "BLE001":
        if _is_cli_path(path):
            return CLASSIFIED, "POLICY:boundary", "easy", "matched"

    # B008 — typer.Option / typer.Argument as default
    if rule == "B008":
        line_text = _read_line(root, path, lineno)
        if "typer.Option(" in line_text or "typer.Argument(" in line_text:
            return CLASSIFIED, "POLICY:typer-default", "easy", "matched"

    # PLR0913 — wiring / bootstrap / factory
    if rule == "PLR0913":
        if _is_wiring_path(path):
            return CLASSIFIED, "POLICY:wiring", "easy", "matched"

    # C901 — migration sequence functions
    if rule == "C901":
        all_lines = _read_lines(root, path)
        if _is_migration_function(all_lines, lineno):
            return CLASSIFIED, "POLICY:migration-sequence", "medium", "matched"

    # F401 in __init__.py — defer to human review
    if rule == "F401" and path.endswith("__init__.py"):
        return NEEDS_REVIEW, None, "needs_review", "f401-init"

    # Fallback
    return NEEDS_REVIEW, None, "needs_review", "not-implemented"


def _is_cli_path(path: str) -> bool:
    """True if path matches CLI top-level heuristic."""
    filename = Path(path).name
    if filename.startswith("cli_"):
        return True
    parts = Path(path).parts
    if "cli" in parts:
        return True
    return False


def _is_wiring_path(path: str) -> bool:
    """True if path is a bootstrap / wiring / factory module."""
    lower = path.lower()
    return (
        "/bootstrap/" in lower
        or lower.startswith("bootstrap/")
        or "wiring" in lower
        or "factory" in lower
    )


def _is_migration_function(lines: list[str], lineno: int) -> bool:
    """True if line at lineno (1-based) contains a migration function def."""
    # Check a window: the target line and a few lines before it
    start = max(0, lineno - 4)
    end = min(len(lines), lineno + 2)
    for line in lines[start:end]:
        stripped = line.strip()
        if re.search(r"\bdef\s+(_atomic_|_migrate_)\w+", stripped):
            return True
    return False


# ---------------------------------------------------------------------------
# Inline suffix writing
# ---------------------------------------------------------------------------

_NOQA_RE = re.compile(r"(#\s*noqa:\s*[A-Z0-9,\s]+)(.*)")
_PYRIGHT_RE = re.compile(r"(#\s*pyright:\s*ignore\[[^\]]*\])(.*)")
_TYPE_IGNORE_RE = re.compile(r"(#\s*type:\s*ignore\[[^\]]*\])(.*)")
_SUFFIX_ALREADY_RE = re.compile(r"[-—]+\s*(POLICY|DEBT):")


def _apply_suffix(line: str, suggestion: str) -> str:
    """Append POLICY/DEBT suffix to the first noqa/pyright/type-ignore on the line."""
    for pattern in (_NOQA_RE, _PYRIGHT_RE, _TYPE_IGNORE_RE):
        m = pattern.search(line)
        if m:
            tail = m.group(2)
            if _SUFFIX_ALREADY_RE.search(tail):
                return line  # idempotent
            suffix = f" — {suggestion}"
            insert_at = m.start(2)
            return line[:insert_at] + suffix + line[insert_at:]
    return line


def _apply_file_edits(p: Path, edits: list[tuple[int, str]]) -> int:
    """Apply all inline suffix edits in one pass. Returns count of lines mutated.

    Duplicate line numbers: warn and keep first (deduplicate).
    """
    seen: set[int] = set()
    deduped: list[tuple[int, str]] = []
    for lineno, sug in edits:
        if lineno in seen:
            print(f"skipped duplicate edit at {p}:{lineno}", file=sys.stderr)
            continue
        seen.add(lineno)
        deduped.append((lineno, sug))
    deduped.sort(key=lambda x: x[0])
    text = p.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines(keepends=True)
    mutated = 0
    for lineno, sug in deduped:
        idx = lineno - 1
        if 0 <= idx < len(lines):
            original = lines[idx].rstrip("\n").rstrip("\r")
            updated = _apply_suffix(original, sug)
            if updated != original:
                eol = lines[idx][len(original):]
                lines[idx] = updated + eol
                mutated += 1
    p.write_text("".join(lines), encoding="utf-8")
    return mutated


# ---------------------------------------------------------------------------
# Registry helpers
# ---------------------------------------------------------------------------


def _parse_frontmatter(content: str) -> dict[str, str]:
    """Extract simple key: value pairs from YAML frontmatter."""
    m = _FRONTMATTER_RE.search(content)
    if not m:
        return {}
    fm_text = m.group(1)
    result: dict[str, str] = {}
    for fm_m in _FM_FIELD_RE.finditer(fm_text):
        result[fm_m.group(1)] = fm_m.group(2).strip()
    return result


def _ensure_registry(debt_dir: Path, slug: str, rules: list[str]) -> None:
    """Create slug.md in debt_dir if it does not exist."""
    if not _SLUG_RE.fullmatch(slug):
        return  # silently skip - caller logged warning
    reg = debt_dir / f"{slug}.md"
    if reg.exists():
        return
    debt_dir.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    primary_rule = sorted(set(rules))[0] if rules else "unknown"
    # Humanize slug: replace _ and - with spaces, capitalize words
    title = slug.replace("_", " ").replace("-", " ").title()
    content = _REGISTRY_TEMPLATE.format(
        slug=slug,
        title=title,
        today=today,
        rule=primary_rule,
    )
    reg.write_text(content, encoding="utf-8")


def _update_index(debt_dir: Path) -> None:
    """Regenerate INDEX.md from all slug files in debt_dir."""
    index_path = debt_dir / "INDEX.md"
    existing = index_path.read_text(encoding="utf-8") if index_path.exists() else ""

    # Preserve header text above the table
    table_start = existing.find("| Slug |")
    if table_start != -1:
        header_text = existing[:table_start]
    else:
        header_text = "# Quality-Debt Registry - INDEX\n\n"

    rows: list[str] = []
    for reg in sorted(debt_dir.glob("*.md")):
        if reg.name == "INDEX.md":
            continue
        content = reg.read_text(encoding="utf-8", errors="replace")
        fm = _parse_frontmatter(content)
        slug = fm.get("slug", reg.stem)
        status = fm.get("status", "open")
        rules = fm.get("rules", "")
        drain_slice = fm.get("drain_slice", "")
        created = fm.get("created", "")
        rows.append(f"| {slug} | {status} | {rules} | - | {drain_slice} | {created} |")

    table = (
        "| Slug | Status | Rules | Sites | Drain slice | Created |\n"
        "|------|--------|-------|-------|-------------|------|\n"
        "<!-- rows inserted here -->\n"
    )
    if rows:
        table += "\n".join(rows) + "\n"

    index_path.write_text(header_text + table, encoding="utf-8")


# ---------------------------------------------------------------------------
# Core classification pass
# ---------------------------------------------------------------------------


def _classify_all(
    rows: list[Row], root: Path
) -> list[tuple[Row, str, str | None, str, str]]:
    """Return list of (row, status, suggestion, fix_class, reason) for UNTAGGED rows."""
    results: list[tuple[Row, str, str | None, str, str]] = []
    for row in rows:
        if row.get("bucket") != "UNTAGGED":
            continue
        status, suggestion, fix_class, reason = _classify_row(row, root)
        results.append((row, status, suggestion, fix_class, reason))
    return results


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

TSV_HEADER = "path\tline\trule\tsuggestion\tfix_class"


def _print_dry_run(
    classified: list[tuple[Row, str, str | None, str, str]],
) -> None:
    """Print TSV + summary to stdout."""
    n_classified = sum(1 for _, s, _, _, _ in classified if s == CLASSIFIED)
    n_needs_review = sum(1 for _, s, _, _, _ in classified if s == NEEDS_REVIEW)
    total = len(classified)
    denom = total - n_needs_review
    ratio = (n_classified / denom) if denom > 0 else 0.0

    # Print summary FIRST so it is not shadowed by TSV rows containing "RATIO"
    # (e.g. "migration-sequence" contains the substring "ratio").
    print(
        f"CLASSIFIED: {n_classified} / UNTAGGED: {total} "
        f"/ NEEDS_REVIEW: {n_needs_review} / RATIO: {ratio:.2f}"
    )

    print(TSV_HEADER)
    for row, _status, suggestion, fix_class, _reason in classified:
        path = row.get("path", "")
        line = row.get("line", "")
        rule = row.get("rule", "")
        sug = suggestion or ""
        print(f"{path}\t{line}\t{rule}\t{sug}\t{fix_class}")


def _run_apply(
    classified: list[tuple[Row, str, str | None, str, str]],
    root: Path,
    report_path: Path,
) -> None:
    """Apply inline edits, registry files, INDEX update, drain queue."""
    debt_dir = root / "artifacts" / "debt"
    debt_dir.mkdir(parents=True, exist_ok=True)

    slug_rules: dict[str, list[str]] = {}
    easy_rows: list[dict[str, Any]] = []

    # Group edits by path for batched file writes (N9)
    path_edits: dict[str, list[tuple[int, str]]] = {}

    for row, status, suggestion, fix_class, reason in classified:
        path = row.get("path", "")
        lineno = int(row.get("line", 1))
        rule = row.get("rule", "")

        if status == CLASSIFIED and suggestion:
            # Guard: skip paths outside repo root
            safe_p = _safe_repo_path(root, path)
            if safe_p is None:
                print(f"skipped path outside root: {path}", file=sys.stderr)
                continue

            # Collect inline edits grouped by path
            path_edits.setdefault(path, []).append((lineno, suggestion))

            # Collect DEBT slugs for registry
            if suggestion.startswith("DEBT:"):
                slug = suggestion[5:]
                slug_rules.setdefault(slug, []).append(rule)

            # Drain queue candidates (easy only)
            if fix_class == "easy":
                s_bucket = (
                    "POLICY" if suggestion.startswith("POLICY:") else "DEBT"
                )
                s_slug = suggestion.split(":", 1)[1] if ":" in suggestion else ""
                easy_rows.append(
                    {
                        "path": path,
                        "line": lineno,
                        "rule": rule,
                        "suggested_bucket": s_bucket,
                        "debt_slug": s_slug,
                        "fix_class": fix_class,
                        "fix_loc_estimate": 1,
                        "reason": reason,
                    }
                )

    # Apply batched file edits (one read+write per file)
    for path_str, edits in path_edits.items():
        safe_p = _safe_repo_path(root, path_str)
        if safe_p is None:
            continue
        if safe_p.exists():
            _apply_file_edits(safe_p, edits)

    # Create DEBT registry files
    for slug, rules in slug_rules.items():
        _ensure_registry(debt_dir, slug, rules)

    # Update INDEX
    _update_index(debt_dir)

    # Write drain queue (cap at 30 easy, sorted deterministically)
    easy_rows.sort(key=lambda r: (r["path"], r["line"]))
    queue = easy_rows[:30]
    queue_path = root / "artifacts" / "quality-debt-drain-queue.json"
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    queue_path.write_text(
        json.dumps(queue, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _default_report() -> Path:
    return Path.cwd() / "artifacts" / "quality-debt-report.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Classify UNTAGGED rows in a quality-debt audit report."
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help=(
            "Path to quality-debt-report.json "
            "(default: artifacts/quality-debt-report.json)"
        ),
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=True,
        help="Print TSV preview to stdout without mutating files (default).",
    )
    mode_group.add_argument(
        "--apply",
        dest="dry_run",
        action="store_false",
        help="Apply inline suffixes, create registry files, write drain queue.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output JSON instead of TSV (dry-run only).",
    )
    args = parser.parse_args(argv)

    report_path: Path = (args.report or _default_report()).resolve()
    if not report_path.exists():
        print(f"ERROR: report not found: {report_path}", file=sys.stderr)
        return 1

    try:
        report: dict[str, Any] = json.loads(report_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"ERROR: cannot read report: {exc}", file=sys.stderr)
        return 1

    rows: list[Row] = report.get("rows", [])

    # Resolve root: use cwd (tests set cwd=tmp_path)
    root = Path.cwd().resolve()

    classified = _classify_all(rows, root)

    if args.dry_run:
        _print_dry_run(classified)
    else:
        _run_apply(classified, root, report_path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
