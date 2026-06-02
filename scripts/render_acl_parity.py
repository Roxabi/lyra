#!/usr/bin/env python3
"""Render effective ACL JSON from acl-matrix.json for CI parity gate.

Reads deploy/nats/acl-matrix.json, computes effective grants (group expansion +
request_reply_flows inbox derivation), and writes a JSON fixture with the same
structure as tests/scripts/fixtures/v2-prod.json (version + request_reply_flows +
identities with effective publish/subscribe arrays).

Usage:
    uv run python scripts/render_acl_parity.py
    uv run python scripts/render_acl_parity.py --dry-run
    uv run python scripts/render_acl_parity.py --output \
        tests/scripts/fixtures/v3-current.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from scripts._acl_models import LoadedMatrix
from scripts._effective import effective_grants
from scripts._loader import load_matrix

DEFAULT_MATRIX = Path("deploy/nats/acl-matrix.json")
DEFAULT_OUTPUT = Path("tests/scripts/fixtures/v3-current.json")


def build_effective_json(matrix: LoadedMatrix) -> dict:
    """Return a dict matching the v2-prod.json fixture with effective grants."""
    eff = effective_grants(matrix)

    identities_out: dict[str, dict] = {}
    for name, identity in matrix["identities"].items():
        if identity["status"] != "active":
            continue
        # Copy original fields so metadata is preserved, then overwrite
        # publish/subscribe with the effective (deduplicated, expanded) lists.
        id_out = dict(identity)
        id_out["publish"] = eff[name][0]
        id_out["subscribe"] = eff[name][1]
        id_out.pop("groups", None)
        identities_out[name] = id_out

    return {
        "version": matrix["version"],
        "request_reply_flows": list(matrix.get("request_reply_flows", [])),
        "identities": identities_out,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Render effective ACL JSON fixture.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print JSON to stdout without writing.",
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
        default=DEFAULT_OUTPUT,
        help="Path to output JSON fixture (default: %(default)s).",
    )
    args = parser.parse_args()

    matrix = load_matrix(args.matrix)
    data = build_effective_json(matrix)
    json_text = json.dumps(data, indent=2) + "\n"

    if args.dry_run:
        print(json_text, end="")
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json_text)
    print(f"Updated fixture: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
