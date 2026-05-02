#!/usr/bin/env python3
"""check_acl_matrix_retired.py — validate lifecycle fields on all acl-matrix.json identities.

Exit 0: all identities valid.
Exit 1: one or more errors printed to stderr.

Error classes (mirrors check-acl-matrix-retired.sh):
  EC-1  Missing status field on any identity      → caught by load_matrix
  EC-2  Missing created_at on any identity         → caught by load_matrix
  EC-3  Bad date format on created_at              → caught by load_matrix
  EC-4  Retired identity without retired_at
  EC-5  Retired identity still referenced in flows
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure repo root is on sys.path when executed directly as a script.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts._loader import load_matrix  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check ACL matrix for retired identity issues"
    )
    parser.add_argument(
        "--matrix",
        type=Path,
        default=Path("deploy/nats/acl-matrix.json"),
    )
    args = parser.parse_args()

    # load_matrix validates EC-1/EC-2/EC-3; exits 1 on failure.
    matrix = load_matrix(args.matrix)

    identities = matrix["identities"]
    flows = matrix.get("request_reply_flows") or []

    # Build set of names referenced in flows (requester or responder).
    flow_names: set[str] = set()
    for flow in flows:
        flow_names.add(flow["requester"])
        flow_names.add(flow["responder"])

    errors: list[str] = []

    for name, identity in identities.items():
        status = identity.get("status", "")

        if status == "retired":
            # EC-4: retired_at must be present.
            if "retired_at" not in identity:
                errors.append(f"ERROR: '{name}' is retired but missing retired_at")

            # EC-5: must not be referenced in any flow.
            if name in flow_names:
                errors.append(
                    f"ERROR: '{name}' is retired but still referenced in request_reply_flows"
                )

    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        sys.exit(1)

    print("ok — acl-matrix lifecycle fields valid")


if __name__ == "__main__":
    main()
