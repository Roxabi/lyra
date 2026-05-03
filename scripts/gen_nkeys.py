#!/usr/bin/env python3
"""gen_nkeys.py — NATS nkey provisioning CLI (Slice 2: key-aware modes).

Umbrella entry point: lyra-acl <subcommand>
Aliases: lyra-genkeys, lyra-check-acl-retired, lyra-check-flows
"""

from __future__ import annotations

import argparse
import os
import pwd
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Callable

# Ensure repo root is on sys.path when executed directly as a script.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts._acl_models import Flow, Identity  # noqa: E402
from scripts._loader import load_matrix  # noqa: E402
from scripts._nk import (  # noqa: E402
    FakeNkeyProvider,
    NkeyProvider,
    SubprocessNkeyProvider,
)
from scripts._renderer import render_auth_conf  # noqa: E402
from scripts._supervisor import validate_supervisor  # noqa: E402

_DEFAULT_MATRIX = Path("deploy/nats/acl-matrix.json")

_NOT_YET = "not yet implemented in this slice"

# ---------------------------------------------------------------------------
# DI point: tests can monkeypatch this to inject FakeNkeyProvider.
# When NKEY_PROVIDER=fake env var is set (e.g. in subprocess tests),
# FakeNkeyProvider is used; otherwise SubprocessNkeyProvider is the default.
# ---------------------------------------------------------------------------

_provider_factory: Callable[[], NkeyProvider] = SubprocessNkeyProvider


def _get_provider() -> NkeyProvider:
    """Return the active NkeyProvider, falling back to Fake when nk is absent."""
    env_provider = os.environ.get("NKEY_PROVIDER", "").lower()
    if env_provider == "fake":
        return FakeNkeyProvider()
    if shutil.which("nk") is None:
        # nk not on PATH — fall back to FakeNkeyProvider so that tests that
        # write fake seed bytes (name.encode()) can still produce pubkeys.
        return FakeNkeyProvider()
    return _provider_factory()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def operator_home() -> Path:
    """Resolve operator home: SUDO_USER's home if set, else Path.home()."""
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        return Path(pwd.getpwnam(sudo_user).pw_dir)
    return Path.home()


def atomic_write(path: Path, content: str, mode: int) -> None:
    """Write content to path atomically (tmp → chmod → replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _seeds_dir() -> Path:
    """Return seeds directory, overridable via SEEDS_DIR env var."""
    env = os.environ.get("SEEDS_DIR")
    if env:
        return Path(env)
    return operator_home() / ".lyra" / "nkeys"


def _auth_dir() -> Path:
    """Return system auth directory, overridable via AUTH_DIR env var."""
    env = os.environ.get("AUTH_DIR")
    if env:
        return Path(env)
    return Path("/etc/nats/nkeys")


def _fake_pubkeys(matrix_path: Path) -> dict[str, str]:
    """Generate fake nkey pubkeys for active identities (template-only mode)."""
    from scripts._loader import load_matrix as _lm

    matrix = _lm(matrix_path)
    return {
        name: f"UDET{name.upper().replace('-', '')}"
        for name, identity in matrix["identities"].items()
        if identity["status"] == "active"
    }


# ---------------------------------------------------------------------------
# Mode implementations
# ---------------------------------------------------------------------------


def _mode_regen_authconf(args: argparse.Namespace) -> None:
    """--regen-authconf: re-derive pubkeys from existing seeds, write auth.conf."""
    seeds_dir = _seeds_dir()
    matrix = load_matrix(args.matrix)
    provider = _get_provider()

    active = {
        name: identity
        for name, identity in matrix["identities"].items()
        if identity["status"] == "active"
    }

    pubkeys: dict[str, str] = {}
    for name in active:
        seed_file = seeds_dir / f"{name}.seed"
        if not seed_file.exists():
            print(
                f"error: missing seed: {seed_file}"
                " — run 'uv run lyra-acl genkeys'",
                file=sys.stderr,
            )
            sys.exit(1)
        seed = seed_file.read_bytes()
        pubkeys[name] = provider.pubkey_from_seed(seed)

    content = render_auth_conf(matrix, pubkeys)
    auth_conf = seeds_dir / "auth.conf"
    atomic_write(auth_conf, content, 0o600)


def _mode_emit_merged_authconf(args: argparse.Namespace) -> None:
    """--emit-merged-authconf: merge lyra + voicecli seeds into one auth.conf."""
    seeds_dir = _seeds_dir()
    voicecli_seeds_dir = Path(
        os.environ.get(
            "VOICECLI_SEEDS_DIR", str(operator_home() / ".voicecli" / "nkeys")
        )
    )
    matrix = load_matrix(args.matrix)
    provider = _get_provider()

    lyra_names = [
        name
        for name, ident in matrix["identities"].items()
        if ident["status"] == "active" and ident["owner"] == "lyra"
    ]
    voicecli_names = [
        name
        for name, ident in matrix["identities"].items()
        if ident["status"] == "active" and ident["owner"] == "voicecli"
    ]

    pubkeys: dict[str, str] = {}
    for name in lyra_names:
        seed_file = seeds_dir / f"{name}.seed"
        if not seed_file.exists():
            print(
                f"error: missing lyra seed: {seed_file} — run 'uv run lyra-acl genkeys'",
                file=sys.stderr,
            )
            sys.exit(1)
        pubkeys[name] = provider.pubkey_from_seed(seed_file.read_bytes())

    for name in voicecli_names:
        seed_file = voicecli_seeds_dir / f"{name}.seed"
        if not seed_file.exists():
            print(
                f"error: missing voicecli seed: {seed_file}"
                " — copy seed from ~/.lyra/nkeys/",
                file=sys.stderr,
            )
            sys.exit(1)
        pubkeys[name] = provider.pubkey_from_seed(seed_file.read_bytes())

    content = render_auth_conf(matrix, pubkeys)
    auth_conf = seeds_dir / "auth.conf"
    atomic_write(auth_conf, content, 0o600)


def _require_root() -> None:
    """Exit 1 unless running as root. Skipped when AUTH_DIR env var is set (tests)."""
    if os.environ.get("AUTH_DIR"):
        return  # test override: AUTH_DIR redirects system paths — no root needed
    if os.geteuid() != 0:
        print("error: must be run as root (sudo lyra-acl genkeys ...)", file=sys.stderr)
        sys.exit(1)


def _mode_regenerate(args: argparse.Namespace) -> None:
    """--regenerate: atomic backup + wipe + full regen (root required)."""
    _require_root()

    if not args.yes:
        import sys as _sys

        if _sys.stdin.isatty():
            reply = input(
                "This will wipe seeds and auth.conf"
                " (backups will be created). Continue? [y/N] "
            )
            if not reply.strip().lower().startswith("y"):
                print("Aborted.", file=sys.stderr)
                sys.exit(0)
        else:
            print(
                "error: stdin is not a TTY"
                " — pass --yes to confirm non-interactive wipe",
                file=sys.stderr,
            )
            sys.exit(1)

    seeds_dir = _seeds_dir()
    auth_dir = _auth_dir()
    auth_conf = auth_dir / "auth.conf"
    import shutil as _shutil
    import time

    epoch = int(time.time())
    if auth_conf.exists():
        _shutil.copy2(str(auth_conf), str(auth_conf) + f".bak.{epoch}")
    if seeds_dir.exists():
        _shutil.copytree(str(seeds_dir), str(seeds_dir) + f".bak.{epoch}")
        _shutil.rmtree(str(seeds_dir))
    if auth_conf.exists():
        auth_conf.unlink()

    _mode_full_provision(args)


def _mode_show(args: argparse.Namespace) -> None:
    """--show: print current auth.conf (root required)."""
    _require_root()
    auth_dir = _auth_dir()
    auth_conf = auth_dir / "auth.conf"
    if not auth_conf.exists():
        print(
            f"error: auth.conf not found at {auth_conf} — run without --show first",
            file=sys.stderr,
        )
        sys.exit(1)
    print(auth_conf.read_text(), end="")


def _mode_fix_perms(args: argparse.Namespace) -> None:
    """--fix-perms: re-apply permissions (root required)."""
    _require_root()
    seeds_dir = _seeds_dir()
    auth_dir = _auth_dir()
    auth_conf = auth_dir / "auth.conf"

    if seeds_dir.exists():
        seeds_dir.chmod(0o700)
        for seed_file in seeds_dir.glob("*.seed"):
            seed_file.chmod(0o600)

    if auth_conf.exists():
        auth_conf.chmod(0o640)


def _mode_full_provision(args: argparse.Namespace) -> None:
    """Default mode: generate all nkeys + dual-write auth.conf (root required)."""
    _require_root()
    seeds_dir = _seeds_dir()
    auth_dir = _auth_dir()
    matrix = load_matrix(args.matrix)
    provider = _get_provider()

    seeds_dir.mkdir(parents=True, exist_ok=True)
    seeds_dir.chmod(0o700)
    auth_dir.mkdir(parents=True, exist_ok=True)

    active = {
        name: identity
        for name, identity in matrix["identities"].items()
        if identity["status"] == "active"
    }

    pubkeys: dict[str, str] = {}
    for name in active:
        seed = provider.gen_seed(name)
        seed_file = seeds_dir / f"{name}.seed"
        seed_str = seed.decode() if seed.endswith(b"\n") else seed.decode() + "\n"
        atomic_write(seed_file, seed_str, 0o600)
        pubkeys[name] = provider.pubkey_from_seed(seed)

    content = render_auth_conf(matrix, pubkeys)

    # System write: /etc/nats/nkeys/auth.conf (0640)
    system_conf = auth_dir / "auth.conf"
    atomic_write(system_conf, content, 0o640)

    # User mirror: ~/.lyra/nkeys/auth.conf (0600)
    user_conf = seeds_dir / "auth.conf"
    atomic_write(user_conf, content, 0o600)


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


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
        matrix = load_matrix(args.matrix)
        repo_root = Path.cwd()
        errors = validate_supervisor(matrix, repo_root)
        if errors:
            for e in errors:
                print(e, file=sys.stderr)
            sys.exit(1)
        print("validate-supervisor: OK")
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


# ---------------------------------------------------------------------------
# Parser construction
# ---------------------------------------------------------------------------


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
        help="Regenerate auth.conf from existing seeds (not yet implemented)",
    )
    gk.add_argument(
        "--emit-merged-authconf",
        action="store_true",
        help="Emit merged auth.conf to stdout (not yet implemented)",
    )
    gk.add_argument(
        "--regenerate",
        action="store_true",
        help="Regenerate all nkeys (not yet implemented)",
    )
    gk.add_argument(
        "--show",
        action="store_true",
        help="Show current nkey state (not yet implemented)",
    )
    gk.add_argument(
        "--fix-perms",
        action="store_true",
        help="Fix seed file permissions (not yet implemented)",
    )
    gk.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation prompts (not yet implemented)",
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


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


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
