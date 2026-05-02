#!/usr/bin/env python3
"""check_request_reply_flows.py — validate request_reply_flows in acl-matrix.json.

Exit 0: all flows resolvable.
Exit 1: drift detected (errors printed to stderr).

Checks per flow:
  - requester identity exists
  - responder identity exists
  - requester publish[] covers the flow subject (NATS-wildcard-aware)
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


def _subject_covered(subject: str, publish: list[str]) -> bool:
    """Return True if *subject* is covered by any grant in *publish*.

    Matching rules (NATS-wildcard-aware, mirrors check-request-reply-flows.sh):
      - Exact match: grant == subject
      - Bare wildcard: grant == ">" (covers everything)
      - Suffix wildcard: grant ends with ".>" and:
          subject == grant[:-2]  (the prefix level itself)
          OR subject starts with grant[:-1]  (any sub-level)
    """
    for grant in publish:
        if grant == subject:
            return True
        if grant == ">":
            return True
        if grant.endswith(".>"):
            prefix = grant[:-1]   # "lyra.foo."
            bare = grant[:-2]     # "lyra.foo"
            if subject == bare or subject.startswith(prefix):
                return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate request_reply_flows in acl-matrix.json"
    )
    parser.add_argument(
        "--matrix",
        type=Path,
        default=Path("deploy/nats/acl-matrix.json"),
    )
    args = parser.parse_args()

    matrix = load_matrix(args.matrix)
    identities = matrix["identities"]
    flows = matrix.get("request_reply_flows") or []

    errors: list[str] = []

    for flow in flows:
        requester = flow["requester"]
        responder = flow["responder"]
        subject = flow.get("subject", "")

        req_exists = requester in identities
        res_exists = responder in identities

        if not req_exists:
            errors.append(f"FAIL: requester '{requester}' not found in identities")

        if not res_exists:
            errors.append(f"FAIL: responder '{responder}' not found in identities")

        if subject and req_exists:
            publish = identities[requester].get("publish", [])
            if not _subject_covered(subject, publish):
                errors.append(
                    f"FAIL: requester '{requester}' publish[] does not cover subject '{subject}'"
                )

    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        sys.exit(1)

    print(f"check-request-reply-flows: OK ({len(flows)} flows)")


if __name__ == "__main__":
    main()
