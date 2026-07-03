#!/usr/bin/env python3
"""Archive-wave tool for ``artifacts/`` deltas (#2218).

Retires closed-issue ``/dev`` deltas (frame / analysis / spec / goal / plan)
from the active ``artifacts/`` tree to ``artifacts/archive/YYYY-MM/``, rewriting
every inbound reference **before** the move so no link is stranded — the
2026-06 wave moved files with a naive grep and left 18 dead links.

Policy SSoT: ``artifacts/README.md``. Rationale: ADR-086 (``artifacts/`` =
deltas only; current truth lives in ``docs/architecture/`` domain pages).

Usage::

    uv run python tools/archive_artifacts_wave.py --dry-run
    uv run python tools/archive_artifacts_wave.py --apply
    uv run python tools/archive_artifacts_wave.py --closed-issues-file closed.json

Census: the issue number is parsed from each candidate basename
(``<issue>-<slug>-<kind>``) or its ``issue:`` frontmatter field, then GitHub is
asked (GraphQL via ``gh``) which are closed. ``--closed-issues-file`` (a JSON
list of issue numbers) bypasses the network so the rewrite logic is testable
offline.

Exit codes:
    0 = clean — dry-run planned with 0 broken links, or ``--apply`` succeeded.
    1 = a reference would be left dangling (plan is not link-clean).
    2 = tool error (bad args, ``gh`` failure).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# Categories swept by the wave. Only top-level files in these dirs are
# candidates — subdirs (plans/TEMPLATE, analyses/archive, …) are left alone.
SWEEP_CATEGORIES: tuple[str, ...] = ("frames", "analyses", "specs", "goal", "plans")

# File suffixes scanned for inbound references (docs, workflows, skills,
# gate scripts, sibling artifacts). Broader than `git grep` on docs/ alone —
# the 2026-06 miss was a basename cited from outside docs/.
SCAN_SUFFIXES: frozenset[str] = frozenset(
    {".md", ".mdx", ".yml", ".yaml", ".sh", ".py", ".txt", ".json", ".toml"}
)

# Directories never descended into when scanning for references or candidates.
SKIP_DIRS: frozenset[str] = frozenset(
    {".git", ".venv", "node_modules", "dist", "build", ".worktrees", ".mypy_cache"}
)

_ISSUE_BASENAME_RE = re.compile(r"^(\d+)-")
_ISSUE_FRONTMATTER_RE = re.compile(r"^issue:\s*\"?(\d+)\"?\s*$", re.MULTILINE)
_DOC_SUFFIXES: frozenset[str] = frozenset({".md", ".mdx"})


@dataclass(frozen=True)
class Candidate:
    """A sweepable delta file and its resolved issue number (if any)."""

    path: Path
    category: str
    basename: str
    issue: int | None


@dataclass(frozen=True)
class Move:
    """A planned archive move plus the path-substring rewrite it implies."""

    src: Path
    dst: Path
    old_tail: str
    new_tail: str
    issue: int


@dataclass
class WavePlan:
    """The full plan: moves, per-file rewrite counts, and any broken links."""

    month: str
    moves: list[Move] = field(default_factory=list)
    rewrites: dict[Path, int] = field(default_factory=dict)
    broken: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Candidate discovery + issue census
# ---------------------------------------------------------------------------


def _extract_issue(path: Path) -> int | None:
    """Return the issue number from the basename prefix or frontmatter."""
    m = _ISSUE_BASENAME_RE.match(path.name)
    if m:
        return int(m.group(1))
    if path.suffix in _DOC_SUFFIXES:
        text = path.read_text(encoding="utf-8", errors="replace")
        fm = _ISSUE_FRONTMATTER_RE.search(text)
        if fm:
            return int(fm.group(1))
    return None


def iter_candidates(root: Path) -> list[Candidate]:
    """List top-level delta files under the swept categories."""
    out: list[Candidate] = []
    for category in SWEEP_CATEGORIES:
        cat_dir = root / "artifacts" / category
        if not cat_dir.is_dir():
            continue
        for path in sorted(cat_dir.iterdir()):
            if not path.is_file() or path.suffix not in _DOC_SUFFIXES:
                continue
            out.append(
                Candidate(
                    path=path,
                    category=category,
                    basename=path.name,
                    issue=_extract_issue(path),
                )
            )
    return out


def fetch_closed_issues(numbers: set[int]) -> set[int]:
    """Ask GitHub which of ``numbers`` are CLOSED (GraphQL via ``gh``)."""
    if not numbers:
        return set()
    fields = "\n".join(
        f"i{n}: issue(number: {n}) {{ number state }}" for n in sorted(numbers)
    )
    query = f"query {{ repository(owner: $owner, name: $name) {{ {fields} }} }}"
    proc = subprocess.run(
        [
            "gh",
            "api",
            "graphql",
            "-F",
            "owner={owner}",
            "-F",
            "name={repo}",
            "-f",
            f"query={query}",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    payload: object = json.loads(proc.stdout)
    closed: set[int] = set()
    repo = _dig(payload, ("data", "repository"))
    if isinstance(repo, dict):
        for node in repo.values():
            if (
                isinstance(node, dict)
                and node.get("state") == "CLOSED"
                and isinstance(node.get("number"), int)
            ):
                closed.add(node["number"])
    return closed


def _dig(payload: object, keys: tuple[str, ...]) -> object:
    cur: object = payload
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def load_closed_issues_file(path: Path) -> set[int]:
    """Load a JSON list of issue numbers (offline census override)."""
    data: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected a JSON list of issue numbers")
    return {int(x) for x in data}


# ---------------------------------------------------------------------------
# Reference scanning + rewrite
# ---------------------------------------------------------------------------


def scan_text_files(root: Path) -> list[Path]:
    """All text files under ``root`` that may cite an artifact path."""
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames if d not in SKIP_DIRS and not _is_worktrees(dirpath, d)
        ]
        for name in filenames:
            p = Path(dirpath) / name
            if p.suffix in SCAN_SUFFIXES:
                out.append(p)
    return sorted(out)


def _is_worktrees(dirpath: str, name: str) -> bool:
    # Skip .claude/worktrees (nested worktrees share the object store).
    return name == "worktrees" and dirpath.endswith(".claude")


def count_refs(files: list[Path], tail: str) -> dict[Path, int]:
    """Map each file to how many times ``tail`` appears in it."""
    hits: dict[Path, int] = {}
    for f in files:
        text = f.read_text(encoding="utf-8", errors="replace")
        n = text.count(tail)
        if n:
            hits[f] = n
    return hits


def _tail(category: str, basename: str) -> str:
    return f"artifacts/{category}/{basename}"


def _new_tail(month: str, basename: str) -> str:
    return f"artifacts/archive/{month}/{basename}"


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def build_plan(root: Path, month: str, closed: set[int]) -> WavePlan:
    """Compute moves, inbound rewrites, and any residual broken links."""
    plan = WavePlan(month=month)
    files = scan_text_files(root)
    rewrite_map: dict[str, str] = {}
    for cand in iter_candidates(root):
        if cand.issue is None or cand.issue not in closed:
            continue
        old_tail = _tail(cand.category, cand.basename)
        new_tail = _new_tail(month, cand.basename)
        dst = root / "artifacts" / "archive" / month / cand.basename
        plan.moves.append(Move(cand.path, dst, old_tail, new_tail, cand.issue))
        rewrite_map[old_tail] = new_tail
    for old_tail in rewrite_map:
        for path, n in count_refs(files, old_tail).items():
            plan.rewrites[path] = plan.rewrites.get(path, 0) + n
    plan.broken = _residual_broken(files, rewrite_map)
    return plan


def _residual_broken(files: list[Path], rewrite_map: dict[str, str]) -> list[str]:
    """Simulate the rewrite in memory; report any old path still referenced.

    Guarantees the '0 broken links' acceptance bar: every occurrence of a
    moved file's old path must be covered by a rewrite before the move lands.
    """
    if not rewrite_map:
        return []
    broken: list[str] = []
    for f in files:
        text = f.read_text(encoding="utf-8", errors="replace")
        for old_tail, new_tail in rewrite_map.items():
            text = text.replace(old_tail, new_tail)
        for old_tail in rewrite_map:
            if old_tail in text:
                broken.append(f"{f}: unrewritten reference to {old_tail}")
    return broken


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def apply_plan(root: Path, plan: WavePlan, use_git: bool = True) -> None:
    """Rewrite every inbound reference, then move each file."""
    rewrite_map = {m.old_tail: m.new_tail for m in plan.moves}
    for path in scan_text_files(root):
        text = path.read_text(encoding="utf-8", errors="replace")
        new_text = text
        for old_tail, new_tail in rewrite_map.items():
            new_text = new_text.replace(old_tail, new_tail)
        if new_text != text:
            path.write_text(new_text, encoding="utf-8")
    for mv in plan.moves:
        _move(root, mv.src, mv.dst, use_git=use_git)


def _move(root: Path, src: Path, dst: Path, use_git: bool) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if use_git:
        subprocess.run(
            ["git", "-C", str(root), "mv", str(src), str(dst)],
            check=True,
            capture_output=True,
            text=True,
        )
    else:
        os.replace(src, dst)


# ---------------------------------------------------------------------------
# Rendering + CLI
# ---------------------------------------------------------------------------


def render_plan(plan: WavePlan) -> str:
    lines: list[str] = []
    lines.append(f"archive wave → artifacts/archive/{plan.month}/")
    if not plan.moves:
        lines.append("  no closed-issue deltas to archive.")
        return "\n".join(lines)
    lines.append(f"  moves ({len(plan.moves)}):")
    for mv in sorted(plan.moves, key=lambda m: m.old_tail):
        lines.append(f"    #{mv.issue:<6} {mv.old_tail} -> {mv.new_tail}")
    total = sum(plan.rewrites.values())
    lines.append(f"  link rewrites: {total} across {len(plan.rewrites)} file(s):")
    for path in sorted(plan.rewrites):
        lines.append(f"    {plan.rewrites[path]:>3}x  {path}")
    return "\n".join(lines)


def _resolve_closed(args: argparse.Namespace, candidates: list[Candidate]) -> set[int]:
    numbers = {c.issue for c in candidates if c.issue is not None}
    if args.closed_issues_file:
        return load_closed_issues_file(Path(args.closed_issues_file)) & numbers
    return fetch_closed_issues(numbers)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--month", help="archive bucket YYYY-MM (default: this month)")
    parser.add_argument("--closed-issues-file", help="JSON list of closed issue #s")
    parser.add_argument("--apply", action="store_true", help="perform the move")
    parser.add_argument("--dry-run", action="store_true", help="plan only (default)")
    parser.add_argument("--no-git", action="store_true", help="os.rename, not git mv")
    args = parser.parse_args(argv)

    root: Path = args.root.resolve()
    month: str = args.month or datetime.now(timezone.utc).strftime("%Y-%m")

    try:
        closed = _resolve_closed(args, iter_candidates(root))
    except (
        subprocess.CalledProcessError,
        json.JSONDecodeError,
        ValueError,
        OSError,
    ) as exc:
        print(f"census failed: {exc}", file=sys.stderr)
        return 2

    plan = build_plan(root, month, closed)
    print(render_plan(plan))

    if plan.broken:
        print("\nBROKEN LINKS — refusing to move:", file=sys.stderr)
        for b in plan.broken:
            print(f"  {b}", file=sys.stderr)
        return 1

    if args.apply:
        apply_plan(root, plan, use_git=not args.no_git)
        print(f"\napplied: {len(plan.moves)} moved, link-clean.")
    else:
        print("\ndry-run: 0 broken links. Re-run with --apply to move.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
