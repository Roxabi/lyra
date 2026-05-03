#!/usr/bin/env python3
"""gen_nkeys.py — NATS nkey provisioning CLI (Slice 2: key-aware modes).

Umbrella entry point: lyra-acl <subcommand>
Aliases: lyra-genkeys, lyra-check-acl-retired, lyra-check-flows
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure repo root is on sys.path when executed directly as a script.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts._acl_models import Flow, Identity  # noqa: E402
from scripts._loader import load_matrix  # noqa: E402
from scripts._modes import (  # noqa: E402
    _mode_emit_merged_authconf,
    _mode_fix_perms,
    _mode_full_provision,
    _mode_regen_authconf,
    _mode_regenerate,
    _mode_show,
    atomic_write,
    operator_home,
)

__all__ = ["atomic_write", "operator_home"]  # re-exported for test imports
from scripts._renderer import render_auth_conf  # noqa: E402
from scripts._supervisor import validate_supervisor  # noqa: E402

_DEFAULT_MATRIX = Path("deploy/nats/acl-matrix.json")


def _cmd_genkeys(args: argparse.Namespace) -> None:
    """Dispatch genkeys sub-flags."""
    if args.template_only:
        matrix = load_matrix(args.matrix)
        pubkeys = {
            name: f"UDET{name.upper().replace('-', '')}"
            for name, identity in matrix["identities"].items()
            if identity["status"] == "active"
        }
        print(render_auth_conf(matrix, pubkeys), end="")
        return

    if args.validate_supervisor:
        _cmd_validate_supervisor(args)
        return

    if args.regen_authconf:
        _mode_regen_authconf(args)
        return

    if args.emit_merged_authconf:
        _mode_emit_merged_authconf(args)
        return

    if args.show:
        _mode_show(args)
        return

    if args.fix_perms:
        _mode_fix_perms(args)
        return

    if args.regenerate:
        _mode_regenerate(args)
        return

    # Default: full provisioning
    _mode_full_provision(args)


def _cmd_validate_supervisor(args: argparse.Namespace) -> None:
    matrix = load_matrix(args.matrix)
    repo_root = Path.cwd()
    errors = validate_supervisor(matrix, repo_root)
    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        sys.exit(1)
    print("validate-supervisor: OK")


def _cmd_check_retired(args: argparse.Namespace) -> None:
    """Run check_acl_matrix_retired logic inline."""
    matrix = load_matrix(args.matrix)
    identities = matrix["identities"]
    flows = matrix.get("request_reply_flows") or []

    flow_names: set[str] = set()
    for flow in flows:
        flow_names.add(flow["requester"])
        flow_names.add(flow["responder"])

    errors: list[str] = []
    for name, identity in identities.items():
        status = identity.get("status", "")
        if status == "retired":
            if "retired_at" not in identity:
                errors.append(f"ERROR: '{name}' is retired but missing retired_at")
            if name in flow_names:
                errors.append(
                    f"ERROR: '{name}' is retired but still referenced"
                    " in request_reply_flows"
                )

    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        sys.exit(1)

    print("ok — acl-matrix lifecycle fields valid")


def _subject_covered(subject: str, publish: list[str]) -> bool:
    """Return True if subject is covered by any NATS wildcard grant in publish."""
    for grant in publish:
        if grant in (subject, ">"):
            return True
        if grant.endswith(".>"):
            prefix = grant[:-1]
            bare = grant[:-2]
            if subject == bare or subject.startswith(prefix):
                return True
    return False


def _flow_errors(flow: Flow, identities: dict[str, Identity]) -> list[str]:
    requester = flow["requester"]
    responder = flow["responder"]
    subject = flow.get("subject", "")
    errors: list[str] = []
    req_exists = requester in identities
    if not req_exists:
        errors.append(f"FAIL: requester '{requester}' not found in identities")
    if responder not in identities:
        errors.append(f"FAIL: responder '{responder}' not found in identities")
    if subject and req_exists:
        publish = identities[requester].get("publish", [])
        if not _subject_covered(subject, publish):
            errors.append(
                f"FAIL: requester '{requester}' publish[] does not cover"
                f" subject '{subject}'"
            )
    return errors


def _cmd_check_flows(args: argparse.Namespace) -> None:
    """Run check_request_reply_flows logic inline."""
    matrix = load_matrix(args.matrix)
    identities = matrix["identities"]
    flows = matrix.get("request_reply_flows") or []

    errors: list[str] = []
    for flow in flows:
        errors.extend(_flow_errors(flow, identities))

    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        sys.exit(1)

    print(f"check-request-reply-flows: OK ({len(flows)} flows)")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lyra-acl",
        description="NATS nkey provisioning and ACL validation CLI",
    )
    sub = parser.add_subparsers(dest="subcommand", metavar="<subcommand>")
    sub.required = True

    # --- genkeys subcommand ---
    gk = sub.add_parser("genkeys", help="Key generation and authconf management")
    gk.add_argument(
        "--matrix",
        type=Path,
        default=_DEFAULT_MATRIX,
        metavar="PATH",
        help="Path to acl-matrix.json (default: deploy/nats/acl-matrix.json)",
    )
    gk.add_argument(
        "--template-only",
        action="store_true",
        help="Render auth.conf to stdout using fake nkeys (no nk binary required)",
    )
    gk.add_argument(
        "--validate-supervisor",
        action="store_true",
        help="Check NATS_NKEY_SEED_PATH wiring in deploy files",
    )
    gk.add_argument(
        "--regen-authconf",
        action="store_true",
        help="Regenerate auth.conf from existing seeds (no new keys generated)",
    )
    gk.add_argument(
        "--emit-merged-authconf",
        action="store_true",
        help="Emit merged lyra+voicecli auth.conf from existing seeds",
    )
    gk.add_argument(
        "--regenerate",
        action="store_true",
        help="Backup + wipe + regenerate all nkeys (root required)",
    )
    gk.add_argument(
        "--show",
        action="store_true",
        help="Print current auth.conf (root required)",
    )
    gk.add_argument(
        "--fix-perms",
        action="store_true",
        help="Re-apply 0600/0640 permissions to seeds and auth.conf (root required)",
    )
    gk.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation prompts (for use with --regenerate in CI/scripts)",
    )
    gk.set_defaults(func=_cmd_genkeys)

    # --- validate-supervisor subcommand ---
    vs = sub.add_parser(
        "validate-supervisor",
        help="Validate NATS_NKEY_SEED_PATH wiring in deploy files",
    )
    vs.add_argument(
        "--matrix",
        type=Path,
        default=_DEFAULT_MATRIX,
        metavar="PATH",
    )
    vs.set_defaults(func=_cmd_validate_supervisor)

    # --- check subcommand (with sub-subcommands) ---
    ck = sub.add_parser("check", help="ACL matrix consistency checks")
    ck_sub = ck.add_subparsers(dest="check_cmd", metavar="{retired,flows}")
    ck_sub.required = True

    ck_retired = ck_sub.add_parser(
        "retired", help="Validate lifecycle fields on retired identities"
    )
    ck_retired.add_argument(
        "--matrix",
        type=Path,
        default=_DEFAULT_MATRIX,
        metavar="PATH",
    )
    ck_retired.set_defaults(func=_cmd_check_retired)

    ck_flows = ck_sub.add_parser("flows", help="Validate request_reply_flows coverage")
    ck_flows.add_argument(
        "--matrix",
        type=Path,
        default=_DEFAULT_MATRIX,
        metavar="PATH",
    )
    ck_flows.set_defaults(func=_cmd_check_flows)

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    args.func(args)


def alias_genkeys() -> None:
    sys.argv.insert(1, "genkeys")
    main()


def _check_retired() -> None:
    sys.argv.insert(1, "check")
    sys.argv.insert(2, "retired")
    main()


def _check_flows() -> None:
    sys.argv.insert(1, "check")
    sys.argv.insert(2, "flows")
    main()


if __name__ == "__main__":
    main()
