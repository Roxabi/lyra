#!/usr/bin/env python3
"""check_request_reply_flows.py — validate request_reply_flows in acl-matrix.json.

Exit 0: all flows resolvable.
Exit 1: drift detected (errors printed to stderr).

Checks per flow:
  - requester identity exists
  - responder identity exists
  - requester publish[] covers the flow subject (NATS-wildcard-aware)
  - responder subscribe[] covers the flow subject (NATS-wildcard-aware)
  - responder effective publish[] covers _inbox.{requester}.> (bidirectional)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure repo root is on sys.path when executed directly as a script.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts._effective import effective_grants, subject_covered  # noqa: E402
from scripts._loader import load_matrix  # noqa: E402


def _validate_flow(
    flow: dict,
    identities: dict,
    eff: dict,
) -> list[str]:
    """Validate a single request-reply flow; return a list of error strings."""
    errors: list[str] = []
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
        if not subject_covered(subject, publish):
            errors.append(
                f"FAIL: requester '{requester}' publish[] does not cover"
                f" subject '{subject}'"
            )

    if subject and res_exists:
        subscribe = identities[responder].get("subscribe", [])
        if not subject_covered(subject, subscribe):
            errors.append(
                f"FAIL: responder '{responder}' subscribe[] does not cover"
                f" subject '{subject}'"
            )

    # Use effective grants (not raw) to check responder can publish to
    # requester's inbox — effective_grants auto-injects _inbox.{requester}.>
    # into responder publish for all flows (step 4 expansion).
    if req_exists and res_exists:
        inbox = f"_inbox.{requester}.>"
        eff_res_pub = eff.get(responder, ([], []))[0]
        if not subject_covered(inbox, eff_res_pub):
            errors.append(
                f"FAIL: responder '{responder}' effective publish[] does not"
                f" cover inbox '{inbox}' for requester '{requester}'"
            )

    return errors


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
    eff = effective_grants(matrix)

    errors: list[str] = []
    for flow in flows:
        errors.extend(_validate_flow(flow, identities, eff))

    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        sys.exit(1)

    print(f"check-request-reply-flows: OK ({len(flows)} flows, bidirectional)")


if __name__ == "__main__":
    main()
