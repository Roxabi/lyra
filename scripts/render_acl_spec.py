#!/usr/bin/env python3
"""Render ACL matrix markdown table from effective grants.

Reads deploy/nats/acl-matrix.json, computes effective grants (group expansion +
request_reply_flows inbox derivation), and renders a markdown table into the
sentinel block of artifacts/specs/706-per-role-nkeys-acls-spec.mdx.

Usage:
    uv run python scripts/render_acl_spec.py
    uv run python scripts/render_acl_spec.py --dry-run
    uv run python scripts/render_acl_spec.py --output path/to/spec.mdx
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from scripts._acl_models import LoadedMatrix
from scripts._effective import effective_grants
from scripts._loader import load_matrix

SENTINEL_BEGIN = "<!-- acl-matrix:begin -->"
SENTINEL_END = "<!-- acl-matrix:end -->"

DEFAULT_MATRIX = Path("deploy/nats/acl-matrix.json")
DEFAULT_SPEC = Path("artifacts/specs/706-per-role-nkeys-acls-spec.mdx")


SUBJECT_FILTERS = ("lyra.", "_inbox.", "$JS.API.", "$KV.")


def _filter_subjects(subjects: set[str]) -> list[str]:
    return sorted(s for s in subjects if s.startswith(SUBJECT_FILTERS))


def render_table(matrix: LoadedMatrix) -> str:
    """Return the markdown table as a string (no trailing newline)."""
    eff = effective_grants(matrix)

    active_ids = [
        name
        for name, identity in matrix["identities"].items()
        if identity["status"] == "active"
    ]

    all_subjects: set[str] = set()
    for _, (pub, sub) in eff.items():
        all_subjects.update(pub)
        all_subjects.update(sub)
    subjects = _filter_subjects(all_subjects)

    lines: list[str] = []
    lines.append("| Subject | " + " | ".join(active_ids) + " |")
    lines.append("|---|" + "|".join(":-:" for _ in active_ids) + "|")

    for subj in subjects:
        row = [f"| `{subj}` |"]
        for id_name in active_ids:
            pub, sub = eff[id_name]
            has_pub = subj in pub
            has_sub = subj in sub
            if has_pub and has_sub:
                row.append(" PUB+SUB |")
            elif has_pub:
                row.append(" PUB |")
            elif has_sub:
                row.append(" SUB |")
            else:
                row.append(" — |")
        lines.append("".join(row))

    return "\n".join(lines)


def _write_spec(table: str, spec_path: Path) -> None:
    if not spec_path.exists():
        print(f"error: spec file not found: {spec_path}", file=sys.stderr)
        sys.exit(1)

    text = spec_path.read_text()
    if SENTINEL_BEGIN not in text or SENTINEL_END not in text:
        print(
            f"error: sentinel markers missing in {spec_path} — cannot update safely",
            file=sys.stderr,
        )
        sys.exit(1)

    out_lines: list[str] = []
    inside = False
    for line in text.splitlines():
        if SENTINEL_BEGIN in line:
            out_lines.append(line)
            inside = True
            continue
        if SENTINEL_END in line:
            out_lines.append(table)
            out_lines.append(line)
            inside = False
            continue
        if not inside:
            out_lines.append(line)

    spec_path.write_text("\n".join(out_lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render ACL matrix table into spec sentinel block."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print table to stdout without writing.",
    )
    parser.add_argument(
        "--matrix",
        type=Path,
        default=DEFAULT_MATRIX,
        help="Path to acl-matrix.json (default: %(default)s).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_SPEC,
        help="Path to spec MDX file (default: %(default)s).",
    )
    args = parser.parse_args()

    matrix = load_matrix(args.matrix)
    table = render_table(matrix)

    if args.dry_run:
        print(table)
        return 0

    _write_spec(table, args.output)
    print(f"Updated sentinel block in {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
