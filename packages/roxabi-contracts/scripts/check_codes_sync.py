"""check_codes_sync.py — Sync KNOWN_CODES (Python) ↔ error-codes.md (docs).

`KNOWN_CODES` in ``roxabi_contracts.errors`` is the single source of truth.
``docs/error-codes.md`` is a generated, human-readable view of the same data.

Usage (from repo root):
    # Verify the MD matches KNOWN_CODES (default mode, CI gate):
    uv run python packages/roxabi-contracts/scripts/check_codes_sync.py

    # Regenerate the MD from KNOWN_CODES (called by pre-commit hook):
    uv run python packages/roxabi-contracts/scripts/check_codes_sync.py --write

Exit codes:
    0  — all codes in sync (check mode) OR file written (write mode)
    1  — drift detected (set diff or field mismatch) (check mode only)
    2  — prerequisite missing (errors.py not yet authored)

Referenced by: packages/roxabi-contracts/docs/error-codes.md
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_SCRIPT_DIR = Path(__file__).parent
_CONTRACTS_ROOT = _SCRIPT_DIR.parent
_MD_PATH = _CONTRACTS_ROOT / "docs" / "error-codes.md"

# ---------------------------------------------------------------------------
# Step 1 — load KNOWN_CODES from Python source
# ---------------------------------------------------------------------------


def _load_python_codes() -> dict[str, object] | None:
    """Import KNOWN_CODES from roxabi_contracts.errors.

    Returns None when the module does not exist yet (T4 not landed).
    """
    try:
        from roxabi_contracts.errors import (
            KNOWN_CODES,  # type: ignore[import-not-found]
        )

        return KNOWN_CODES  # type: ignore[return-value]
    except ModuleNotFoundError:
        return None


# ---------------------------------------------------------------------------
# Step 2 — parse error-codes.md
# ---------------------------------------------------------------------------

# Matches a non-header, non-separator table row.
# Codes use dot-notation: e.g. ``transport.timeout``, ``worker.crash``.
# The pattern anchors on a leading lowercase letter (or digit) followed by
# any word chars / dots — deliberately excludes the literal header cell
# ``code`` only when it's surrounded by pipes with ``retryable`` next, but
# relying on the ``(true|false)`` capture in column 2 is the real filter:
# the header row has ``retryable`` there (not ``true``/``false``) so it
# falls through cleanly.  Separator rows (``|---|...``) also don't match.
#
# Pattern groups: (code, retryable, description)
_ROW_RE = re.compile(r"^\|\s*([a-z][a-z0-9_.]*)\s*\|\s*(true|false)\s*\|\s*(.*?)\s*\|$")

# Matches a domain section heading.
# Headings may use a glob suffix for readability: ``## transport.*``
# The captured group is stripped of any trailing ``.*`` before use so that
# ``transport.*`` → ``transport``, matching CodeMeta.domain exactly.
_HEADING_RE = re.compile(r"^##\s+(\S+)")


def _parse_md(path: Path) -> list[dict[str, str]] | None:
    """Parse every code row from error-codes.md.

    Returns a list of dicts with keys: code, domain, retryable, description.
    Returns None when the file does not exist yet (T5 not landed).

    Parser rules:
    - Section heading ``## <word>`` sets current domain for subsequent rows.
    - Header row (``| code | retryable | description |``) and separator rows
      (``|---|...``) are skipped via the _ROW_RE anchoring on [A-Z][A-Z0-9_]*.
    - Inline code in description cells (backticks, brackets) is kept verbatim —
      string equality is checked against codemeta.description post-strip only.
    - A row appearing before any ## heading gets domain="" and will mismatch
      any real CodeMeta, surfacing the authoring error explicitly.
    """
    if not path.exists():
        return None

    rows: list[dict[str, str]] = []
    current_domain = ""

    for line in path.read_text(encoding="utf-8").splitlines():
        heading_m = _HEADING_RE.match(line)
        if heading_m:
            # Strip trailing ``.*`` glob suffix used in headings like ``## transport.*``
            raw_heading = heading_m.group(1)
            current_domain = raw_heading.removesuffix(".*")
            continue

        row_m = _ROW_RE.match(line)
        if row_m:
            rows.append(
                {
                    "code": row_m.group(1),
                    "retryable": row_m.group(2),  # "true" or "false"
                    "description": row_m.group(3).strip(),
                    "domain": current_domain,
                }
            )

    return rows


# ---------------------------------------------------------------------------
# Step 3 — compare
# ---------------------------------------------------------------------------


def _compare(  # noqa: C901
    py_codes: dict[str, object],
    md_rows: list[dict[str, str]],
) -> list[str]:
    """Return a list of drift messages.  Empty list → in sync."""
    issues: list[str] = []

    py_set = set(py_codes.keys())
    md_map: dict[str, dict[str, str]] = {}
    md_duplicates: list[str] = []

    for row in md_rows:
        code = row["code"]
        if code in md_map:
            md_duplicates.append(code)
        md_map[code] = row

    if md_duplicates:
        for code in md_duplicates:
            issues.append(f"  DUPLICATE in MD: {code}")

    md_set = set(md_map.keys())

    only_python = sorted(py_set - md_set)
    only_md = sorted(md_set - py_set)

    for code in only_python:
        issues.append(f"  ONLY_IN_PYTHON: {code}")
    for code in only_md:
        issues.append(f"  ONLY_IN_MD:     {code}")

    # Field spot-check for codes present on both sides
    for code in sorted(py_set & md_set):
        meta = py_codes[code]
        row = md_map[code]

        # domain
        py_domain: str = getattr(meta, "domain", "")
        md_domain: str = row["domain"]
        if py_domain != md_domain:
            issues.append(
                f"  FIELD_MISMATCH: {code}.domain  py={py_domain!r}  md={md_domain!r}"
            )

        # retryable
        py_retryable: bool | None = getattr(meta, "default_retryable", None)
        md_retryable_str: str = row["retryable"]
        md_retryable: bool = md_retryable_str == "true"
        if py_retryable is not None and py_retryable != md_retryable:
            issues.append(
                f"  FIELD_MISMATCH: {code}.retryable"
                f"  py={py_retryable}  md={md_retryable_str!r}"
            )

        # description (stripped string equality)
        py_desc: str = getattr(meta, "description", "")
        md_desc: str = row["description"]
        if py_desc.strip() != md_desc:
            issues.append(
                f"  FIELD_MISMATCH: {code}.description\n"
                f"    py={py_desc.strip()!r}\n"
                f"    md={md_desc!r}"
            )

    return issues


# ---------------------------------------------------------------------------
# Step 4 — render MD from Python (generator)
# ---------------------------------------------------------------------------

# ruff: noqa: E501 — preamble is rendered verbatim into Markdown; line length is irrelevant for users
_PREAMBLE = """\
# WorkerError code registry

> Auto-generated from `roxabi_contracts.errors.KNOWN_CODES`.
> Do not edit by hand — run `uv run python packages/roxabi-contracts/scripts/check_codes_sync.py --write` to regenerate.
> The pre-commit `codes-sync` hook regenerates this file automatically when `errors.py` changes.
"""

_POSTAMBLE = "See ADR-066 for design rationale.\n"


def _render_md(py_codes: dict[str, object]) -> str:
    """Render error-codes.md content from KNOWN_CODES.

    Domain order = first-seen in KNOWN_CODES iteration (preserves Python
    insertion order). Codes within a domain follow insertion order too.
    """
    domains: dict[str, list[tuple[str, object]]] = {}
    for code, meta in py_codes.items():
        domain: str = getattr(meta, "domain", "")
        domains.setdefault(domain, []).append((code, meta))

    out: list[str] = [_PREAMBLE]
    for domain, entries in domains.items():
        out.append(f"## {domain}.*\n")
        out.append("| code | retryable | description |")
        out.append("|------|-----------|-------------|")
        for code, meta in entries:
            retryable_str = (
                "true" if getattr(meta, "default_retryable", False) else "false"
            )
            description: str = getattr(meta, "description", "").strip()
            out.append(f"| {code} | {retryable_str} | {description} |")
        out.append("")  # trailing blank between domain sections
    out.append(_POSTAMBLE)
    return "\n".join(out).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _check_mode(py_codes: dict[str, object]) -> int:
    md_rows = _parse_md(_MD_PATH)
    if md_rows is None:
        print(f"SKIP: prerequisite not yet available: {_MD_PATH}")
        return 2

    issues = _compare(py_codes, md_rows)
    if not issues:
        n = len(py_codes)
        print(f"OK: {n} code{'s' if n != 1 else ''} in sync")
        return 0

    print("DRIFT: codes are out of sync:")
    for line in issues:
        print(line)
    print(
        "\nFix: run `uv run python packages/roxabi-contracts/scripts/"
        "check_codes_sync.py --write` to regenerate the MD."
    )
    return 1


def _write_mode(py_codes: dict[str, object]) -> int:
    rendered = _render_md(py_codes)
    existing = _MD_PATH.read_text(encoding="utf-8") if _MD_PATH.exists() else ""
    if existing == rendered:
        print(f"OK: {_MD_PATH.name} already up-to-date ({len(py_codes)} codes)")
        return 0
    _MD_PATH.write_text(rendered, encoding="utf-8")
    print(f"WROTE: {_MD_PATH} ({len(py_codes)} codes)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Regenerate error-codes.md from KNOWN_CODES (no-op if already in sync).",
    )
    args = parser.parse_args()

    py_codes = _load_python_codes()
    if py_codes is None:
        print("SKIP: prerequisite not yet available: roxabi_contracts.errors")
        return 2

    if args.write:
        return _write_mode(py_codes)
    return _check_mode(py_codes)


if __name__ == "__main__":
    sys.exit(main())
