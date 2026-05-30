#!/usr/bin/env python3
"""Scan docs and CLAUDE.md files for dead backtick references.

Checks src/... paths, module paths, and CamelCase symbols against the codebase.
Exit 0 = clean. Exit 1 = new dead references found. Exit 2 = script error.

Usage:
    python tools/check_doc_drift.py [--root ROOT] [--baseline PATH] [--update-baseline]
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Patterns & constants
# ---------------------------------------------------------------------------

_BACKTICK_RE = re.compile(r"`([^`\n]+)`")
_SRC_PATH_RE = re.compile(r"^(src|packages)/[\w./\-]+$")
_MODULE_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]+)+(\.[A-Za-z]\w*)?$")
_CAMEL_RE = re.compile(r"^[A-Z][a-zA-Z0-9]*[a-z][a-zA-Z0-9]*$")
_HISTORICAL_RE = re.compile(
    r"deleted|removed|superseded|renamed|formerly|no longer"
    r"|legacy|historical|#\d+ deleted",
    re.IGNORECASE,
)

# Known module prefixes to check (others are third-party — skip)
_KNOWN_PREFIXES = frozenset(
    ["lyra", "roxabi_nats", "roxabi_contracts", "roxabi_blobs", "roxabi_vault"]
)

# CamelCase symbols to skip: Python builtins, stdlib exceptions, generic terms,
# and known external package names that won't resolve in src/
_SKIP_SYMBOLS = frozenset(
    # stdlib exceptions + external-package symbols that won't resolve in src/
    "KeyError ValueError TypeError RuntimeError OSError AttributeError "
    "NotImplementedError StopAsyncIteration DeprecationWarning UserWarning "
    "StopIteration Exception BaseException ImportError FileNotFoundError "
    "PermissionError TimeoutError ConnectionError OverflowError IndexError "
    "NameError UnicodeDecodeError UnicodeEncodeError "
    # generic / external terms
    "PascalCase CamelCase GitHub Discord Telegram FastAPI Pydantic Python "
    "TypeVar Protocol Optional Union Dict List Tuple Set Any "
    # known external pkg symbols (nats-py, anthropic)
    "NoRespondersError BucketNotFoundError InputJsonDelta".split()
)


def _default_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _default_baseline(root: Path) -> Path:
    return root / "tools" / "doc_drift_baseline.txt"


# ---------------------------------------------------------------------------
# Scan targets
# ---------------------------------------------------------------------------


def _collect_claude_mds(root: Path, seen: set[Path], out: list[Path]) -> None:
    """Append all non-hidden CLAUDE.md files under root's search dirs."""

    def add(p: Path) -> None:
        if p not in seen and p.is_file():
            seen.add(p)
            out.append(p)

    for sr in (root, root / "src", root / "packages", root / "plugins"):
        if not sr.is_dir():
            continue
        for cm in sorted(sr.rglob("CLAUDE.md")):
            if any(p.startswith(".") for p in cm.relative_to(root).parts):
                continue
            add(cm)


def _collect_scan_files(root: Path) -> list[Path]:
    seen: set[Path] = set()
    out: list[Path] = []

    def add(p: Path) -> None:
        if p not in seen and p.is_file():
            seen.add(p)
            out.append(p)

    arch = root / "docs" / "architecture"
    if arch.is_dir():
        for ext in ("*.md", "*.mdx"):
            for f in sorted(arch.rglob(ext)):
                add(f)
    add(root / "docs" / "ARCHITECTURE.md")
    standards = root / "docs" / "standards"
    if standards.is_dir():
        for f in sorted(standards.rglob("*.md")):
            add(f)
    _collect_claude_mds(root, seen, out)
    return out


# ---------------------------------------------------------------------------
# Token classification
# ---------------------------------------------------------------------------


def _classify_token(token: str) -> str | None:
    """Return 'path', 'module', 'symbol', or None (skip)."""
    t = token.strip()
    if not t or " " in t or "\t" in t or len(t) > 120:
        return None
    if _SRC_PATH_RE.match(t):
        return "path"
    if "." in t and _MODULE_RE.match(t):
        prefix = t.split(".")[0]
        if prefix in _KNOWN_PREFIXES:
            return "module"
        return None
    if _CAMEL_RE.match(t):
        upper = sum(1 for c in t if c.isupper())
        if upper >= 2 and t not in _SKIP_SYMBOLS:
            return "symbol"
    return None


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def _resolve_path(root: Path, token: str) -> bool:
    return (root / token).exists()


def _resolve_module(root: Path, token: str) -> bool:
    rel = Path(*token.split("."))
    cands: list[Path] = []
    src = root / "src"
    if src.is_dir():
        cands += [src / rel, src / rel.with_suffix(".py")]
    pkg_root = root / "packages"
    if pkg_root.is_dir():
        for pd in pkg_root.iterdir():
            ps = pd / "src"
            if ps.is_dir():
                cands += [ps / rel, ps / rel.with_suffix(".py")]
    return any(c.exists() for c in cands)


def _resolve_symbol(root: Path, token: str) -> bool:
    """Grep for class/assignment definitions of token in src/ and packages/."""
    dirs = [str(d) for d in (root / "src", root / "packages") if d.is_dir()]
    if not dirs:
        return False
    for pat in (f"class {token}", f"{token} =", f"{token}("):
        try:
            if (
                subprocess.run(
                    ["grep", "-rqF", "--", pat, *dirs], capture_output=True
                ).returncode
                == 0
            ):
                return True
        except OSError:
            return False
    return False


def _resolve_token(root: Path, token: str, kind: str) -> bool:
    if kind == "path":
        return _resolve_path(root, token)
    if kind == "module":
        return _resolve_module(root, token)
    return _resolve_symbol(root, token)  # kind == "symbol"


# ---------------------------------------------------------------------------
# Baseline
# ---------------------------------------------------------------------------


def _load_baseline(path: Path) -> frozenset[str]:
    if not path.exists():
        return frozenset()
    return frozenset(
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    )


def _baseline_key(relpath: str, token: str) -> str:
    return f"{relpath}::{token}"


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------

Violation = tuple[str, int, str, str]  # (relpath, lineno, token, reason)


def _scan_file(
    root: Path, path: Path, baseline: frozenset[str]
) -> tuple[list[Violation], list[Violation]]:
    new_v: list[Violation] = []
    base_v: list[Violation] = []
    relpath = path.relative_to(root).as_posix()
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return [], []
    for lineno, line in enumerate(lines, 1):
        if "<!-- drift-ignore -->" in line:
            continue
        tokens = _BACKTICK_RE.findall(line)
        if not tokens:
            continue
        is_historical = bool(_HISTORICAL_RE.search(line))
        for token in tokens:
            kind = _classify_token(token)
            if kind is None or is_historical:
                continue
            if _resolve_token(root, token, kind):
                continue
            reason = f"not found in src/ ({kind})"
            key = _baseline_key(relpath, token)
            (base_v if key in baseline else new_v).append(
                (relpath, lineno, token, reason)
            )
    return new_v, base_v


def scan(
    root: Path, baseline: frozenset[str]
) -> tuple[list[Violation], list[Violation]]:
    all_new: list[Violation] = []
    all_base: list[Violation] = []
    for f in _collect_scan_files(root):
        n, b = _scan_file(root, f, baseline)
        all_new.extend(n)
        all_base.extend(b)
    return all_new, all_base


# ---------------------------------------------------------------------------
# Baseline update
# ---------------------------------------------------------------------------


def _write_baseline(path: Path, violations: list[Violation]) -> None:
    keys = sorted({_baseline_key(r, t) for r, _, t, _ in violations})
    hdr = (
        "# doc_drift_baseline.txt — burn-down list of known doc-rot\n"
        "# Tracked in epic #1530. Pre-existing violations to purge over time.\n"
        "# Format: relpath::token  (sorted, one per line)\n"
        "# DO NOT add new entries — fix the doc or add a historical annotation.\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(hdr + "\n".join(keys) + ("\n" if keys else ""), encoding="utf-8")
    print(f"Baseline written: {len(keys)} entries → {path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Scan docs and CLAUDE.md files for dead backtick references. "
            "Exit 0 = clean. Exit 1 = new violations. Exit 2 = script error."
        )
    )
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--baseline", type=Path, default=None)
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        default=False,
        help="Rewrite baseline from all current violations, then exit 0.",
    )
    args = parser.parse_args(argv)

    root: Path = (args.root or _default_root()).resolve()
    baseline_path: Path = args.baseline or _default_baseline(root)
    baseline = _load_baseline(baseline_path)

    new_violations, baselined = scan(root, baseline)

    if args.update_baseline:
        _write_baseline(baseline_path, new_violations + baselined)
        return 0

    if new_violations:
        for relpath, lineno, token, reason in sorted(new_violations):
            print(f"{relpath}:{lineno} → `{token}` → {reason}")
        n = len(new_violations)
        b = len(baselined)
        print(f"\n{n} dead reference(s) found ({b} baselined).", file=sys.stderr)
        print(
            "Suppress with: historical annotation (deleted/removed/...) "
            "or <!-- drift-ignore -->. Fix the doc to clear the violation.",
            file=sys.stderr,
        )
        return 1

    b = len(baselined)
    print(f"doc-drift: OK — 0 new violations ({b} in burn-down baseline).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
