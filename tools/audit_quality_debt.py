#!/usr/bin/env python3
"""Scans for `DEBT:<slug>` suppression markers.

Always exits 0; warns to stderr on untagged or stale-reference markers in src/.

Usage:
    python tools/audit_quality_debt.py --root <DIR> --out <PATH>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

Row = dict[str, Any]
StaleRef = dict[str, Any]

ALL_SOURCES = [
    "noqa",
    "pyright-ignore",
    "type-ignore",
    "importlinter",
    "file-exemptions",
    "folder-exemptions",
]

_SUFFIX_RE = re.compile(r"[-—]+\s*DEBT:([a-z0-9][a-z0-9_-]*)")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_NOQA_RE = re.compile(r"#\s*noqa:\s*([A-Z0-9,\s]+)(.*)")
_PYRIGHT_RE = re.compile(r"#\s*pyright:\s*ignore\[([^\]]*)\](.*)")
_TYPE_IGNORE_RE = re.compile(r"#\s*type:\s*ignore\[([^\]]*)\](.*)")
_IGNORE_IMPORTS_RE = re.compile(r"^ignore_imports\s*=")
_INDENTED_RE = re.compile(r"^\s+(\S.*)")
_SECTION_HEADER_RE = re.compile(r"^\[")
_INI_KEY_RE = re.compile(r"^\w[\w.-]*\s*=")
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)^---\s*\n", re.DOTALL | re.MULTILINE)
_STATUS_RE = re.compile(r"^status:\s*(\S+)", re.MULTILINE)


def _parse_suffix(tail: str) -> tuple[str, str | None]:
    """Return (bucket, slug_or_none) from a trailing comment."""
    m = _SUFFIX_RE.search(tail)
    if not m:
        return "UNTAGGED", None
    return "DEBT", m.group(1)


def _finalize_row(r: Row, bucket: str, slug: str | None) -> Row:
    """Attach optional slug field to a row dict in-place and return it."""
    if bucket == "DEBT" and slug:
        r["slug"] = slug
    return r


def _base_row(source: str, path_str: str, bucket: str) -> Row:
    return {"source": source, "path": path_str, "bucket": bucket}


def _split_comment(text: str) -> tuple[str, str]:
    if "#" in text:
        a, b = text.split("#", 1)
        return a.strip(), b
    return text.strip(), ""


def _scan_py(root: Path, py_file: Path) -> list[Row]:
    rows: list[Row] = []
    rel = py_file.relative_to(root).as_posix()
    patterns = [
        (_NOQA_RE, "noqa"),
        (_PYRIGHT_RE, "pyright-ignore"),
        (_TYPE_IGNORE_RE, "type-ignore"),
    ]
    for lineno, raw in enumerate(py_file.read_text(encoding="utf-8").splitlines(), 1):
        for pattern, source in patterns:
            m = pattern.search(raw)
            if not m:
                continue
            rules_str, tail = m.group(1), m.group(2)
            bucket, slug = _parse_suffix(tail)
            for rule in [r.strip() for r in rules_str.split(",") if r.strip()]:
                row = _base_row(source, rel, bucket)
                row["line"] = lineno
                row["rule"] = rule
                rows.append(_finalize_row(row, bucket, slug))
    return rows


def _scan_importlinter(root: Path) -> list[Row]:
    """Parse ``.importlinter`` ``ignore_imports`` blocks.

    importlinter rejects trailing inline comments on entry lines, so the
    DEBT suffix must live on a preceding indented comment-only line
    that serves as a section header. We track that header's slug and
    apply it to subsequent untagged entries in the same block.
    """
    il_path = root / ".importlinter"
    if not il_path.exists():
        return []
    rows: list[Row] = []
    in_block = False
    section_bucket = "UNTAGGED"
    section_slug: str | None = None
    for lineno, raw in enumerate(il_path.read_text(encoding="utf-8").splitlines(), 1):
        if _IGNORE_IMPORTS_RE.match(raw):
            in_block = True
            section_bucket, section_slug = "UNTAGGED", None
            continue
        if in_block:
            # Close block only on a section header or a different INI key.
            # Blank lines and continuation comments stay inside the block.
            if _SECTION_HEADER_RE.match(raw) or _INI_KEY_RE.match(raw):
                in_block = False
                continue
            m = _INDENTED_RE.match(raw)
            if not m:
                continue  # blank or non-matching line — preserve state
            entry, tail = _split_comment(m.group(1))
            bucket, slug = _parse_suffix(tail)
            if not entry:
                if bucket != "UNTAGGED":
                    section_bucket, section_slug = bucket, slug
                continue
            if bucket == "UNTAGGED" and section_bucket != "UNTAGGED":
                bucket, slug = section_bucket, section_slug
            row = _base_row("importlinter", entry, bucket)
            row["line"] = lineno
            rows.append(_finalize_row(row, bucket, slug))
    return rows


def _scan_exemption(root: Path, rel_path: str, source: str) -> list[Row]:
    path = root / rel_path
    if not path.exists():
        return []
    rows: list[Row] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        entry, tail = _split_comment(stripped)
        bucket, slug = _parse_suffix(tail)
        row = _base_row(source, entry, bucket)
        row["line"] = lineno
        rows.append(_finalize_row(row, bucket, slug))
    return rows


def _registry_status(root: Path, slug: str) -> str | None:
    if not _SLUG_RE.fullmatch(slug):
        return "open"  # malformed slug — treat as no-op (audit will report as stale)
    reg = root / "artifacts" / "debt" / f"{slug}.md"
    try:
        reg_resolved = reg.resolve()
        debt_dir = (root / "artifacts" / "debt").resolve()
        if not reg_resolved.is_relative_to(debt_dir):
            return "open"
    except (OSError, ValueError):
        return "open"
    if not reg.exists():
        return None
    content = reg.read_text(encoding="utf-8")
    fm = _FRONTMATTER_RE.search(content)
    if not fm:
        return "open"
    sm = _STATUS_RE.search(fm.group(1))
    return sm.group(1) if sm else "open"


def _stale_refs(rows: list[Row], root: Path) -> list[StaleRef]:
    stale: list[StaleRef] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if row["bucket"] != "DEBT":
            continue
        slug = row.get("slug", "")
        if not slug:
            continue
        key = (row["path"], slug)
        if key in seen:
            continue
        seen.add(key)
        status = _registry_status(root, slug)
        if status in (None, "drained"):
            reason = "missing" if status is None else "drained"
            stale.append(
                {
                    "path": row["path"],
                    "line": row.get("line"),
                    "slug": slug,
                    "reason": reason,
                }
            )
    return stale


def _counts(rows: list[Row]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for row in rows:
        rule = row.get("rule") or row.get("path", "")
        bucket = row["bucket"]
        label = row.get("slug") or "__none__"
        result.setdefault(rule, {}).setdefault(bucket, {})
        result[rule][bucket][label] = result[rule][bucket].get(label, 0) + 1
    return result


def _print_summary(rows: list[Row], stale: list[StaleRef]) -> None:
    counter: Counter[tuple[str, str, str]] = Counter()
    for row in rows:
        label = row.get("slug") or "-"
        k = (row.get("rule") or "-", row["bucket"], label)
        counter[k] += 1
    print(f"{'RULE':<20} {'BUCKET':<10} {'SLUG':<28} {'N':>4}")
    print("-" * 65)
    for (rule, bucket, label), cnt in sorted(counter.items()):
        print(f"{rule:<20} {bucket:<10} {label:<28} {cnt:>4}")
    print(f"\nTotal rows: {len(rows)}  Stale refs: {len(stale)}")


_PY_SCAN_SKIP = {"__pycache__", ".venv", "tests", "packages"}


def scan(root: Path) -> tuple[list[Row], list[StaleRef]]:
    rows: list[Row] = []
    for py_file in sorted(root.rglob("*.py")):
        parts = py_file.relative_to(root).parts
        if any(p.startswith(".") or p in _PY_SCAN_SKIP for p in parts):
            continue
        rows.extend(_scan_py(root, py_file))
    rows.extend(_scan_importlinter(root))
    rows.extend(_scan_exemption(root, "tools/file_exemptions.txt", "file-exemptions"))
    rows.extend(
        _scan_exemption(root, "tools/folder_exemptions.txt", "folder-exemptions")
    )
    return rows, _stale_refs(rows, root)


def _default_root() -> Path:
    return Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit quality-debt suppression annotations."
    )
    parser.add_argument(
        "--root", type=Path, default=_default_root(), help="Repo root to scan"
    )
    parser.add_argument("--out", type=Path, required=True, help="JSON output path")
    args = parser.parse_args(argv)
    root: Path = args.root.resolve()

    rows, stale = scan(root)
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "sources": ALL_SOURCES,
        "rows": rows,
        "stale_references": stale,
        "counts_by_rule_bucket_slug": _counts(rows),
    }
    out: Path = args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    _print_summary(rows, stale)

    for r in rows:
        if r["bucket"] == "UNTAGGED" and r["path"].startswith("src/"):
            loc = f"{r['path']}:{r.get('line', '?')} ({r.get('rule', '?')})"
            print(f"warn: untagged suppression — {loc}", file=sys.stderr)
    for s in stale:
        if s["path"].startswith("src/"):
            loc = f"{s['path']}:{s.get('line', '?')}"
            ref = f"DEBT:{s['slug']} ({s['reason']})"
            print(f"warn: stale DEBT reference — {loc} → {ref}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
