#!/usr/bin/env python3
"""Classify UNTAGGED quality-debt rows from audit report.

Usage:
    python tools/classify_quality_debt.py [--report PATH] [--dry-run|--apply] [--json]
    python tools/classify_quality_debt.py --migrate-policy [--root PATH]

Default report: artifacts/quality-debt-report.json (resolved from cwd).
Default mode: --dry-run.

Output modes:
    --dry-run          Print TSV preview + summary line to stdout (default).
    --dry-run --json   Print JSON array to stdout:
                       [{path, line, rule, suggestion, fix_class, reason}, ...]
                       Summary line is suppressed; parse the array length directly.
    --apply            Apply inline suffixes, create registry files, write drain queue.
    --migrate-policy   One-shot mode: rewrite DEBT:<old-tag> markers in src/lyra/ to
                       DEBT:<slug> using the built-in mapping table.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

import yaml

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
drain_slice: TBD  # TODO: set to actual slice (e.g., P2a) when promoting
parent_slice: TBD  # TODO: set to actual parent issue (e.g., '#1162') when promoting
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

# Mapping from old POLICY:<tag> to new DEBT:<slug> — used by both the classifier
# suggestion strings and the --migrate-policy rewrite pass.
POLICY_TO_DEBT_SLUG: dict[str, str] = {
    "boundary": "boundary-broad-catch",
    "wiring": "wiring-bootstrap-deps",
    "defensive-narrow": "defensive-narrow-payloads",
    "re-export": "re-export-init",
    "typer-default": "typer-default-option",
    "migration-sequence": "migration-sequence-bootstrap",
    "protocol-private": "protocol-private-ducktyping",
    "module-level-patch": "module-level-patch-fixtures",
}

# Rule 8 + Rule 9: fixed suggestion per rule (no path inspection required).
# rule -> suggestion string; fix_class is always "easy" for these.
_RULE_SUGGESTION_MAP: dict[str, str] = {
    # Rule 6: E402 — imports after side effects (no path filter needed)
    "E402": "DEBT:module-level-patch-fixtures",
    # Rule 8: pyright/mypy type annotation rules
    "reportUnnecessaryIsInstance": "DEBT:defensive-narrow-payloads",
    "reportUnusedClass": "DEBT:protocol-private-ducktyping",
    "union-attr": "DEBT:defensive-narrow-payloads",
    "misc": "DEBT:defensive-narrow-payloads",
    "type-arg": "DEBT:defensive-narrow-payloads",
    "import-untyped": "DEBT:defensive-narrow-payloads",
    "PLC0414": "DEBT:re-export-init",
    "ARG002": "DEBT:protocol-private-ducktyping",
    # Rule 9: PLC0415 — lazy/deferred import
    "PLC0415": "DEBT:plc0415-deferred-import",
    # Rule 11: residual lint singletons -> DEBT slugs (T6 may consolidate)
    "I001": "DEBT:lint-residual",
    "E501": "DEBT:lint-residual",
    "A002": "DEBT:lint-residual",
    "return-value": "DEBT:lint-residual",
}

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)^---\s*\n", re.DOTALL | re.MULTILINE)
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
    """Read a specific line (1-based) from a source file; return '' on error."""
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


def _classify_complexity_rule(
    path: str, lineno: int, root: Path
) -> tuple[str, str | None, str, str]:
    """Shared logic for C901/PLR0915/PLR0912 (complexity rules).

    Priority: _atomic_/_migrate_ def > bootstrap entry func > dispatcher path > review.
    """
    all_lines = _read_lines(root, path)
    if _is_migration_function(all_lines, lineno):
        return CLASSIFIED, "DEBT:migration-sequence-bootstrap", "medium", "matched"
    if _is_bootstrap_entry_function(path, all_lines, lineno):
        return CLASSIFIED, "DEBT:migration-sequence-bootstrap", "medium", "matched"
    if _is_dispatcher_path(path):
        return CLASSIFIED, "DEBT:wiring-bootstrap-deps", "easy", "matched"
    # Fallback: residual complexity that wasn't classifiable by structure ->
    # DEBT slug. T6 may split into per-rule slugs later.
    return CLASSIFIED, "DEBT:complexity-residual", "medium", "fallback"


def _classify_row(row: Row, root: Path) -> tuple[str, str | None, str, str]:
    """Return (status, suggestion, fix_class, reason).

    status: 'classified' | 'needs_review'
    suggestion: e.g. 'DEBT:boundary-broad-catch' or None
    fix_class: 'easy' | 'medium' | 'needs_review'
    reason: 'matched' | 'f401-init' | 'not-implemented'
    """
    rule: str = row.get("rule", "")
    path: str = row.get("path", "")
    lineno: int = int(row.get("line", 1))

    # Rules 8/9: lookup table — fixed suggestion, no path/line inspection.
    suggestion = _RULE_SUGGESTION_MAP.get(rule)
    if suggestion is not None:
        return CLASSIFIED, suggestion, "easy", "matched"

    # Rules 3/4/7: complexity rules — migration-sequence + wiring fallback.
    if rule in ("C901", "PLR0915", "PLR0912"):
        return _classify_complexity_rule(path, lineno, root)

    # Rule 5: F401 — re-export (init.py preferred; non-init also treated as
    # re-export since intentional unused imports are the dominant pattern).
    if rule == "F401":
        return CLASSIFIED, "DEBT:re-export-init", "easy", "matched"

    # Rule 1: BLE001 — boundary. Path-specific match preferred; otherwise
    # fallback to boundary (every BLE001 with an existing noqa is by definition
    # an acknowledged top-level exception handler).
    if rule == "BLE001":
        return CLASSIFIED, "DEBT:boundary-broad-catch", "easy", "matched"

    # B008 — typer default argument.
    if rule == "B008":
        line_text = _read_line(root, path, lineno)
        if "typer.Option(" in line_text or "typer.Argument(" in line_text:
            return CLASSIFIED, "DEBT:typer-default-option", "easy", "matched"

    # Rule 2: PLR0913 — wiring. Path-specific match preferred; otherwise
    # fallback to wiring (every PLR0913 is a high-arg-count constructor by
    # definition, which is the structural shape DEBT:wiring-bootstrap-deps covers).
    if rule == "PLR0913":
        return CLASSIFIED, "DEBT:wiring-bootstrap-deps", "easy", "matched"

    # Fallback: truly unknown rules -> needs_review.
    return NEEDS_REVIEW, None, "needs_review", "not-implemented"


def _is_boundary_path(path: str) -> bool:
    """True if path is a CLI, adapter, bootstrap, command-dispatcher, or tools boundary.

    Replaces the old _is_cli_path with broader coverage:
      - cli/ directory or cli_* filenames (original)
      - src/lyra/adapters/** (channel boundary event loops)
      - src/lyra/bootstrap/** (supervisor entry points)
      - basenames matching command/event dispatchers
      - src/lyra/tools/** (CLI script entry points)
    """
    parts = Path(path).parts
    filename = Path(path).stem  # without extension

    # Original cli heuristics
    if Path(path).name.startswith("cli_"):
        return True
    if "cli" in parts:
        return True

    # Adapters and bootstrap directories
    lower = path.lower()
    if "/adapters/" in lower or "/bootstrap/" in lower:
        return True

    # Tools directory — anchored to src/lyra/tools/ to avoid matching repo-level
    # tools/ (e.g., tools/classify_quality_debt.py itself) when scan scope broadens.
    if "src/lyra/tools/" in lower or "/lyra/tools/" in lower:
        return True

    # Command/event dispatcher basenames
    _BOUNDARY_BASENAME_RE = re.compile(
        r"^(command_loader|builtin_commands|hub_.+|.+_listener)$"
    )
    if _BOUNDARY_BASENAME_RE.fullmatch(filename):
        return True

    return False


# Keep old name as alias for backward compatibility with any external callers
_is_cli_path = _is_boundary_path


def _is_wiring_path(path: str) -> bool:
    """True if path is a bootstrap/wiring/factory/dispatcher/builder/loader module."""
    lower = path.lower()
    basename = Path(path).stem.lower()

    # Original path-level heuristics
    if (
        "/bootstrap/" in lower
        or lower.startswith("bootstrap/")
        or "wiring" in lower
        or "factory" in lower
    ):
        return True

    # Rule 2 extension: basename patterns for wiring modules
    _WIRING_BASENAME_RE = re.compile(
        r"^(.+_dispatch|.+_builder|.+_loader|.+_seeder|authenticator|.+_listener|.+_pipeline)$"
    )
    if _WIRING_BASENAME_RE.fullmatch(basename):
        return True

    return False


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


def _is_bootstrap_entry_function(path: str, lines: list[str], lineno: int) -> bool:
    """True if in bootstrap/** and function is a known entry-point pattern.

    Rule 3a/4a: bootstrap path + def bootstrap_*|main|run|setup_*|*_standalone.
    """
    lower = path.lower()
    if "/bootstrap/" not in lower and not lower.startswith("bootstrap/"):
        return False
    start = max(0, lineno - 4)
    end = min(len(lines), lineno + 2)
    for line in lines[start:end]:
        stripped = line.strip()
        if re.search(
            r"\bdef\s+(bootstrap_\w+|main|run|setup_\w+|\w+_standalone)\b", stripped
        ):
            return True
    return False


def _is_dispatcher_path(path: str) -> bool:
    """True if basename matches router/dispatcher complexity patterns.

    Rule 3b/4b: dispatcher/pipeline/processor/normalize/outbound/emitter basenames.
    """
    basename = Path(path).stem.lower()
    _DISPATCHER_RE = re.compile(
        r"^(.+_dispatch|.+_pipeline|.+_processor|.+_normalize|.+_outbound|.+_emitter)$"
    )
    return bool(_DISPATCHER_RE.fullmatch(basename))


# ---------------------------------------------------------------------------
# Inline suffix writing
# ---------------------------------------------------------------------------

_NOQA_RE = re.compile(r"(#\s*noqa:\s*[A-Z0-9,\s]+)(.*)")
_PYRIGHT_RE = re.compile(r"(#\s*pyright:\s*ignore\[[^\]]*\])(.*)")
_TYPE_IGNORE_RE = re.compile(r"(#\s*type:\s*ignore\[[^\]]*\])(.*)")
_SUFFIX_ALREADY_RE = re.compile(r"[-—]+\s*(POLICY|DEBT):")


def _apply_suffix(line: str, suggestion: str) -> str:
    """Append DEBT suffix to the first noqa/pyright/type-ignore on the line."""
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
                eol = lines[idx][len(original) :]
                lines[idx] = updated + eol
                mutated += 1
    p.write_text("".join(lines), encoding="utf-8")
    return mutated


# ---------------------------------------------------------------------------
# Policy-to-DEBT migration
# ---------------------------------------------------------------------------

# Matches an existing POLICY:<tag> suffix on a suppression marker line.
# Captures the leading dash(es)/em-dash and optional whitespace as <prefix>.
_POLICY_SUFFIX_RE = re.compile(r"(?P<prefix>[-—]+\s*)POLICY:(?P<tag>[a-z][a-z-]*)")


def _migrate_policy_markers(root: Path) -> tuple[int, int]:
    """Rewrite POLICY:<tag> suffixes in src/lyra/**/*.py to DEBT:<slug>.

    Returns (rewritten, unmapped) counts.
    Logs unmapped tags to stderr; leaves those lines unchanged.
    Idempotent: DEBT: markers are unchanged.
    """
    src_root = root / "src" / "lyra"
    rewritten = 0
    unmapped = 0

    for py_file in sorted(src_root.rglob("*.py")):
        try:
            original_text = py_file.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            print(f"skipped unreadable file {py_file}: {exc}", file=sys.stderr)
            continue

        lines = original_text.splitlines(keepends=True)
        changed = False

        for idx, line in enumerate(lines):
            lineno = idx + 1
            m = _POLICY_SUFFIX_RE.search(line)
            if m is None:
                continue
            tag = m.group("tag")
            slug = POLICY_TO_DEBT_SLUG.get(tag)
            if slug is None:
                print(
                    f"unmapped POLICY tag: {tag} at {py_file}:{lineno}",
                    file=sys.stderr,
                )
                unmapped += 1
                continue
            replacement = m.group("prefix") + f"DEBT:{slug}"
            lines[idx] = line[: m.start()] + replacement + line[m.end() :]
            rewritten += 1
            changed = True

        if changed:
            py_file.write_text("".join(lines), encoding="utf-8")

    return rewritten, unmapped


# ---------------------------------------------------------------------------
# Registry helpers
# ---------------------------------------------------------------------------


def _parse_frontmatter(content: str) -> dict[str, Any]:
    """Parse YAML frontmatter. List values (e.g. multi-rule `rules:`) are preserved."""
    m = _FRONTMATTER_RE.search(content)
    if not m:
        return {}
    try:
        data = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


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
        rules_val = fm.get("rules", "")
        rules = (
            ", ".join(str(r) for r in rules_val)
            if isinstance(rules_val, list)
            else str(rules_val)
        )
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
    as_json: bool = False,
) -> None:
    """Print dry-run output to stdout.

    as_json=False (default): TSV rows + summary line.
    as_json=True: JSON array [{path, line, rule, suggestion, fix_class, reason}].
                  Summary line is omitted; callers inspect array length directly.
    """
    if as_json:
        records = []
        for row, _status, suggestion, fix_class, reason in classified:
            records.append(
                {
                    "path": row.get("path", ""),
                    "line": row.get("line", ""),
                    "rule": row.get("rule", ""),
                    "suggestion": suggestion or "",
                    "fix_class": fix_class,
                    "reason": reason,
                }
            )
        print(json.dumps(records, indent=2, ensure_ascii=False))
        return

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
                s_bucket = "DEBT"
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
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help=("Repository root directory (default: cwd). Used by --migrate-policy."),
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
    mode_group.add_argument(
        "--migrate-policy",
        dest="migrate_policy",
        action="store_true",
        default=False,
        help=(
            "One-shot mode: scan src/lyra/**/*.py and rewrite POLICY:<tag> suffixes "
            "to DEBT:<slug> using the built-in mapping table. "
            "Prints summary to stdout; logs unmapped tags to stderr. "
            "Returns non-zero if any unmapped tags are found."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help=(
            "Output JSON array instead of TSV (dry-run only). "
            "Emits [{path, line, rule, suggestion, fix_class, reason}] on stdout. "
            "Summary line is omitted in this mode."
        ),
    )
    args = parser.parse_args(argv)

    # Resolve root (used by --migrate-policy and classify pipeline)
    root = (args.root or Path.cwd()).resolve()

    # --migrate-policy takes precedence: runs its own pipeline and returns.
    if getattr(args, "migrate_policy", False):
        rewritten, unmapped_count = _migrate_policy_markers(root)
        scanned = sum(1 for _ in (root / "src" / "lyra").rglob("*.py"))
        print(
            f"migrate-policy: scanned={scanned} files, "
            f"rewritten={rewritten} lines, unmapped={unmapped_count}"
        )
        return 1 if unmapped_count > 0 else 0

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

    classified = _classify_all(rows, root)

    if args.dry_run:
        _print_dry_run(classified, as_json=args.json)
    else:
        _run_apply(classified, root, report_path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
