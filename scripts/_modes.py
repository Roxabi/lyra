"""Mode implementations for gen_nkeys.py — #1017 Slice 2 helpers + provisioning."""

from __future__ import annotations

import argparse
import getpass
import grp
import os
import pwd
import shutil
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Callable, cast

from factory.paths import factory_data_dir
from scripts._acl_models import ExternalDeploy, LoadedMatrix
from scripts._loader import load_matrix
from scripts._nk import (
    NkeyProvider,
    SubprocessNkeyProvider,
)
from scripts._renderer import parse_auth_conf, render_auth_conf

_provider_factory: Callable[[], NkeyProvider] = SubprocessNkeyProvider


def _get_provider() -> NkeyProvider:
    """Return the active NkeyProvider; exit 1 with install hint when nk is absent."""
    provider = _provider_factory()
    provider.ensure_available()
    return provider


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
    """Return seeds directory, overridable via SEEDS_DIR env var.

    Default: ``factory_data_dir()/"nkeys"`` — honors ``ROXABI_FACTORY_DIR`` and
    matches ``cli_ops.py``, ``install.sh``, and ``Makefile``.
    Override: set ``SEEDS_DIR`` to an absolute path (used by tests and one-off
    operator overrides).
    """
    env = os.environ.get("SEEDS_DIR")
    if env:
        return Path(env)
    return factory_data_dir() / "nkeys"


def _auth_dir() -> Path:
    """Return system auth directory, overridable via AUTH_DIR env var."""
    env = os.environ.get("AUTH_DIR")
    if env:
        return Path(env)
    return Path("/etc/nats/nkeys")


def _etc_nats_write_enabled() -> bool:
    """Return True when the opt-in /etc/nats dual-write path is active.

    Enabled by: ``FACTORY_ACL_WRITE_ETC_NATS=1`` environment variable.
    The host ``nats.service`` is retired; auth.conf is now delivered via Podman
    secret (``factory-nats-auth``).  The /etc/nats/nkeys write path is vestigial
    and opt-in only.  Normal seed generation is rootless and writes exclusively
    to ``factory_data_dir()/nkeys``.
    """
    return os.environ.get("FACTORY_ACL_WRITE_ETC_NATS", "0") == "1"


def _require_root() -> None:
    """Exit 1 unless running as root.

    Called only when the opt-in /etc/nats write path is active
    (``FACTORY_ACL_WRITE_ETC_NATS=1``).  Skipped when ``AUTH_DIR`` env var
    is set (test override: AUTH_DIR redirects system paths to a tmp dir).

    Sanctioned rootless paths: ``--regen-authconf`` and ``--add-identity``.
    For full seed generation without /etc/nats writes, run without sudo;
    ``FACTORY_ACL_WRITE_ETC_NATS=1`` opt-in is only needed when the legacy
    host nats.service is still active on the target machine.
    """
    if os.environ.get("AUTH_DIR"):
        return  # test override: AUTH_DIR redirects system paths — no root needed
    if os.geteuid() != 0:
        print(
            "error: writing /etc/nats/nkeys requires root.\n"
            "Re-run with: sudo FACTORY_ACL_WRITE_ETC_NATS=1 factory-acl genkeys\n"
            "(default rootless seed generation needs no sudo — just run:"
            " factory-acl genkeys)\n"
            "To re-derive auth.conf from existing seeds: factory-acl genkeys"
            " --regen-authconf\n"
            "To provision a single new identity: factory-acl genkeys"
            " --add-identity NAME",
            file=sys.stderr,
        )
        sys.exit(1)


def _operator_uid_gid() -> tuple[int, int]:
    """Return (uid, gid) of the operator (SUDO_USER if set, else current user)."""
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        pw = pwd.getpwnam(sudo_user)
        return pw.pw_uid, pw.pw_gid
    return os.getuid(), os.getgid()


def _operator_user() -> str:
    """Return invoking operator login.

    Preserves SUDO_USER under sudo, falls back to current process user.
    """
    return os.environ.get("SUDO_USER") or getpass.getuser()


def _emit_external_manifest(
    externals: Sequence[tuple[str, ExternalDeploy]],
    seeds_dir: Path,
) -> None:
    """Print scp commands to stderr — one per external identity."""
    print(
        "⚠ External seeds require manual fan-out"
        " (seeds + auth.conf already committed):",
        file=sys.stderr,
    )
    user = _operator_user()
    for name, deploy in externals:
        src = seeds_dir / f"{name}.seed"
        target = f"{user}@{deploy['host']}:{deploy['target_path']}"
        print(f"  scp {src} {target}", file=sys.stderr)


def _handle_externals(
    externals: Sequence[tuple[str, ExternalDeploy]],
    seeds_dir: Path,
    args: argparse.Namespace,
) -> None:
    """Emit manifest and exit 2 when external identities require manual fan-out.

    No-op when externals is empty or --ack-external-distribution is set.
    Must be called OUTSIDE any try/except BaseException to avoid triggering
    rollback on SystemExit(2).
    """
    if not externals:
        return
    _emit_external_manifest(externals, seeds_dir)
    if not getattr(args, "ack_external_distribution", False):
        sys.exit(2)


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
                f"error: missing seed: {seed_file} — run 'uv run factory-acl genkeys'",
                file=sys.stderr,
            )
            sys.exit(1)
        seed = seed_file.read_bytes()
        pubkeys[name] = provider.pubkey_from_seed(seed)

    content = render_auth_conf(matrix, pubkeys)
    auth_conf = seeds_dir / "auth.conf"
    atomic_write(auth_conf, content, 0o600)


def _add_identity_validate(
    name: str, matrix_path: Path, matrix: LoadedMatrix, seeds_dir: Path
) -> None:
    """Validate name + status + other-seed presence (exits non-zero on failure)."""
    if name not in matrix["identities"]:
        print(
            f"error: identity '{name}' not declared in {matrix_path} — add it first",
            file=sys.stderr,
        )
        sys.exit(1)

    identity = matrix["identities"][name]
    if identity["status"] != "active":
        print(
            f"error: identity '{name}' has status '{identity['status']}';"
            " --add-identity only provisions active identities",
            file=sys.stderr,
        )
        sys.exit(1)

    active_others = [
        n
        for n, ident in matrix["identities"].items()
        if ident["status"] == "active" and n != name
    ]
    for other in active_others:
        if not (seeds_dir / f"{other}.seed").exists():
            print(
                f"error: cannot render auth.conf — missing seed for active identity"
                f" '{other}'; run full provision first (--regen-authconf has the"
                " same missing-seed guard)",
                file=sys.stderr,
            )
            sys.exit(1)


def _add_identity_detect_state(name: str, seeds_dir: Path) -> str:
    """Inspect filesystem and return 'noop', 'repaired', or 'added'.

    Block-presence uses parse_auth_conf (not substring match) to avoid
    prefix collisions like 'hub' matching inside '# hub-extra' (#1361 review B1).
    """
    seed_present = (seeds_dir / f"{name}.seed").exists()
    auth_conf_path = seeds_dir / "auth.conf"
    block_present = False
    if auth_conf_path.exists():
        parsed = parse_auth_conf(auth_conf_path.read_text())
        block_present = any(u.comment_name == name for u in parsed.users)
    if seed_present and block_present:
        return "noop"
    if seed_present:
        return "repaired"
    return "added"


def _add_identity_write(
    name: str,
    state: str,
    seeds_dir: Path,
    matrix: LoadedMatrix,
    provider: NkeyProvider,
) -> None:
    """Gather pubkeys, gen seed if added, render + write auth.conf (user mirror)."""
    seed_file = seeds_dir / f"{name}.seed"
    pubkeys: dict[str, str] = {}

    active_others = [
        n
        for n, ident in matrix["identities"].items()
        if ident["status"] == "active" and n != name
    ]
    for other in active_others:
        other_bytes = (seeds_dir / f"{other}.seed").read_bytes()
        pubkeys[other] = provider.pubkey_from_seed(other_bytes)

    if state == "added":
        seed = provider.gen_seed(name)
        seed_str = seed.decode() if seed.endswith(b"\n") else seed.decode() + "\n"
        atomic_write(seed_file, seed_str, 0o600)
        pubkeys[name] = provider.pubkey_from_seed(seed)
    else:
        # repaired: seed exists — reuse, do NOT regenerate
        pubkeys[name] = provider.pubkey_from_seed(seed_file.read_bytes())

    atomic_write(seeds_dir / "auth.conf", render_auth_conf(matrix, pubkeys), 0o600)


def _mode_add_identity(args: argparse.Namespace) -> None:
    """--add-identity NAME: rootless single-identity provisioning + auth.conf re-render.

    Parent pattern: _mode_regen_authconf. Writes only to _seeds_dir()/.
    Never calls _require_root(); never touches _auth_dir().
    Emits STATE=noop|repaired|added on stdout.
    """
    seeds_dir = _seeds_dir()
    matrix = load_matrix(args.matrix)
    name = args.add_identity
    provider = _get_provider()

    _add_identity_validate(name, args.matrix, matrix, seeds_dir)
    state = _add_identity_detect_state(name, seeds_dir)

    if state == "noop":
        print(
            f"identity '{name}' already provisioned and present in auth.conf;"
            " no changes",
            file=sys.stderr,
        )
        print("STATE=noop")
        return

    _add_identity_write(name, state, seeds_dir, matrix, provider)

    if state == "repaired":
        print(
            f"identity '{name}' seed exists but auth.conf missing block;"
            " re-rendered auth.conf",
            file=sys.stderr,
        )
    # state == "added": no stderr message on normal success path.

    print(f"STATE={state}")


def _mode_emit_merged_authconf(args: argparse.Namespace) -> None:
    """--emit-merged-authconf: merge factory + voicecli seeds into one auth.conf."""
    seeds_dir = _seeds_dir()
    voicecli_seeds_dir = Path(
        os.environ.get(
            "VOICECLI_SEEDS_DIR", str(operator_home() / ".voicecli" / "nkeys")
        )
    )
    matrix = load_matrix(args.matrix)
    provider = _get_provider()

    factory_names = [
        name
        for name, ident in matrix["identities"].items()
        if ident["status"] == "active" and ident["owner"] == "factory"
    ]
    voicecli_names = [
        name
        for name, ident in matrix["identities"].items()
        if ident["status"] == "active" and ident["owner"] == "voicecli"
    ]

    pubkeys: dict[str, str] = {}
    for name in factory_names:
        seed_file = seeds_dir / f"{name}.seed"
        if not seed_file.exists():
            print(
                f"error: missing factory seed: {seed_file}"
                " — run 'uv run factory-acl genkeys'",
                file=sys.stderr,
            )
            sys.exit(1)
        pubkeys[name] = provider.pubkey_from_seed(seed_file.read_bytes())

    for name in voicecli_names:
        seed_file = voicecli_seeds_dir / f"{name}.seed"
        if not seed_file.exists():
            print(
                f"error: missing voicecli seed: {seed_file}"
                " — copy seed from the voiceCLI host (see VOICECLI_SEEDS_DIR)",
                file=sys.stderr,
            )
            sys.exit(1)
        pubkeys[name] = provider.pubkey_from_seed(seed_file.read_bytes())

    content = render_auth_conf(matrix, pubkeys)
    auth_conf = seeds_dir / "auth.conf"
    atomic_write(auth_conf, content, 0o600)


def _confirm_wipe() -> None:
    """Prompt operator to confirm destructive wipe; exit non-zero on refusal."""
    if sys.stdin.isatty():
        reply = input(
            "This will wipe seeds and auth.conf"
            " (backups will be created). Continue? [y/N] "
        )
        if not reply.strip().lower().startswith("y"):
            print("Aborted.", file=sys.stderr)
            sys.exit(0)
    else:
        print(
            "error: stdin is not a TTY — pass --yes to confirm non-interactive wipe",
            file=sys.stderr,
        )
        sys.exit(1)


def _backup_seeds(seeds_dir: Path, epoch: int) -> str | None:
    """Back up and wipe seeds_dir; return backup path or None if dir absent."""
    if not seeds_dir.exists():
        return None
    backup = str(seeds_dir) + f".bak.{epoch}"
    shutil.copytree(str(seeds_dir), backup)
    shutil.rmtree(str(seeds_dir))
    return backup


def _backup_etc_auth(epoch: int) -> str | None:
    """Back up and unlink /etc/nats/nkeys/auth.conf; return backup path or None."""
    auth_conf = _auth_dir() / "auth.conf"
    if not auth_conf.exists():
        return None
    backup = str(auth_conf) + f".bak.{epoch}"
    shutil.copy2(str(auth_conf), backup)
    auth_conf.unlink()
    return backup


def _restore_on_failure(
    seeds_dir: Path,
    backup_seeds: str | None,
    backup_auth: str | None,
    write_etc: bool,
) -> None:
    """Restore backups after a failed provision attempt.

    Unconditionally restores whenever a backup exists. A mid-loop failure in
    _mode_full_provision can leave seeds_dir/auth.conf partially repopulated
    before raising, so "already exists" must not be read as "already
    restored, skip" — any partial state is wiped first, then the backup is
    copied back in.
    """
    if backup_seeds and Path(backup_seeds).exists():
        if seeds_dir.exists():
            shutil.rmtree(seeds_dir)
        shutil.copytree(backup_seeds, str(seeds_dir))
    if write_etc and backup_auth:
        auth_conf = _auth_dir() / "auth.conf"
        if Path(backup_auth).exists():
            if auth_conf.exists():
                auth_conf.unlink()
            shutil.copy2(backup_auth, str(auth_conf))


def _mode_regenerate(args: argparse.Namespace) -> None:
    """--regenerate: atomic backup + wipe + full regen.

    Rootless by default — writes exclusively to ``factory_data_dir()/nkeys``.
    When ``FACTORY_ACL_WRITE_ETC_NATS=1`` is set, also backs up and wipes
    ``/etc/nats/nkeys/auth.conf`` (requires root).

    Sanctioned rootless paths for incremental changes: ``--regen-authconf``
    and ``--add-identity`` (no wipe, no root).
    """
    import time

    write_etc = _etc_nats_write_enabled()
    if write_etc:
        _require_root()

    if not args.yes:
        _confirm_wipe()

    seeds_dir = _seeds_dir()
    epoch = int(time.time())
    # _backup_etc_auth runs FIRST (non-destructive copy) so that if it raises,
    # seeds_dir has not yet been wiped and no rollback is needed.
    backup_auth = _backup_etc_auth(epoch) if write_etc else None
    backup_seeds = _backup_seeds(seeds_dir, epoch)

    externals: list[tuple[str, ExternalDeploy]] = []
    try:
        externals = _mode_full_provision(args)
    except BaseException:
        _restore_on_failure(seeds_dir, backup_seeds, backup_auth, write_etc)
        raise
    # OUTSIDE try/except — safe to exit without triggering rollback
    _handle_externals(externals, seeds_dir, args)


def _mode_show(args: argparse.Namespace) -> None:
    """--show: print current auth.conf.

    Default (rootless): reads ``factory_data_dir()/nkeys/auth.conf`` (the
    Podman-secret source).  When ``FACTORY_ACL_WRITE_ETC_NATS=1`` is set,
    reads from ``/etc/nats/nkeys/auth.conf`` instead (requires root).

    Sanctioned rootless path: ``factory-acl genkeys --show``
    (reads the operator-owned copy in ``factory_data_dir()/nkeys``).
    """
    if _etc_nats_write_enabled():
        _require_root()
        auth_conf = _auth_dir() / "auth.conf"
    else:
        auth_conf = _seeds_dir() / "auth.conf"

    if not auth_conf.exists():
        print(
            f"error: auth.conf not found at {auth_conf}"
            " — run 'factory-acl genkeys' or 'factory-acl genkeys --regen-authconf'"
            " first",
            file=sys.stderr,
        )
        sys.exit(1)
    print(auth_conf.read_text(), end="")


def _mode_fix_perms(args: argparse.Namespace) -> None:
    """--fix-perms: re-apply permissions and ownership.

    Default (rootless): re-applies permissions on ``factory_data_dir()/nkeys``
    (operator-owned seeds + auth.conf).  When ``FACTORY_ACL_WRITE_ETC_NATS=1``
    is set, also fixes ``/etc/nats/nkeys/auth.conf`` ownership (requires root).

    Sanctioned rootless path: ``factory-acl genkeys --fix-perms``
    (re-applies 0700/0600 on operator-owned nkeys dir without sudo).
    """
    write_etc = _etc_nats_write_enabled()
    if write_etc:
        _require_root()

    seeds_dir = _seeds_dir()
    uid, gid = _operator_uid_gid()

    if seeds_dir.exists():
        seeds_dir.chmod(0o700)
        os.chown(seeds_dir, uid, gid)
        for seed_file in seeds_dir.glob("*.seed"):
            seed_file.chmod(0o600)
            os.chown(seed_file, uid, gid)
        auth_conf = seeds_dir / "auth.conf"
        if auth_conf.exists():
            auth_conf.chmod(0o600)
            os.chown(auth_conf, uid, gid)

    if write_etc:
        auth_dir = _auth_dir()
        auth_conf = auth_dir / "auth.conf"
        if auth_conf.exists():
            auth_conf.chmod(0o640)
            if not os.environ.get("AUTH_DIR"):
                nats_gid = grp.getgrnam("nats").gr_gid
                os.chown(auth_conf, 0, nats_gid)


def _mode_full_provision(args: argparse.Namespace) -> list[tuple[str, ExternalDeploy]]:
    """Default mode: generate all nkeys + write auth.conf to seeds_dir (rootless).

    The /etc/nats/nkeys dual-write is opt-in via ``FACTORY_ACL_WRITE_ETC_NATS=1``
    (vestigial: host ``nats.service`` is retired; auth.conf is delivered to the
    ``factory-nats`` container via Podman secret ``factory-nats-auth``).  Root is
    only required when the opt-in /etc/nats write path is active.

    Normal path: rootless, writes exclusively to ``factory_data_dir()/nkeys``.
    Operator-owned seeds are created with mode 0600.

    Returns list of (name, deploy) tuples for external identities found in active set.
    Caller is responsible for fan-out manifest + exit code (must run OUTSIDE
    _mode_regenerate's try/except BaseException rollback block).

    Sanctioned rootless paths: ``--regen-authconf`` and ``--add-identity``.
    """
    write_etc = _etc_nats_write_enabled()
    if write_etc:
        _require_root()

    seeds_dir = _seeds_dir()
    matrix = load_matrix(args.matrix)
    provider = _get_provider()
    uid, gid = _operator_uid_gid()

    seeds_dir.mkdir(parents=True, exist_ok=True)
    seeds_dir.chmod(0o700)
    os.chown(seeds_dir, uid, gid)

    if write_etc:
        auth_dir = _auth_dir()
        auth_dir.mkdir(parents=True, exist_ok=True)
        auth_dir.chmod(0o750)

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
        os.chown(seed_file, uid, gid)
        pubkeys[name] = provider.pubkey_from_seed(seed)

    content = render_auth_conf(matrix, pubkeys)

    if write_etc:
        # Opt-in system write: /etc/nats/nkeys/auth.conf (0640, root:nats)
        # Only needed when the legacy host nats.service is still active.
        # Set FACTORY_ACL_WRITE_ETC_NATS=1 to enable.
        auth_dir = _auth_dir()
        system_conf = auth_dir / "auth.conf"
        atomic_write(system_conf, content, 0o640)
        if not os.environ.get("AUTH_DIR"):
            nats_gid = grp.getgrnam("nats").gr_gid
            os.chown(system_conf, 0, nats_gid)

    # Primary write: factory_data_dir()/nkeys/auth.conf (0600, operator-owned)
    # This is the source file for the factory-nats-auth Podman secret.
    user_conf = seeds_dir / "auth.conf"
    atomic_write(user_conf, content, 0o600)
    os.chown(user_conf, uid, gid)

    externals: list[tuple[str, ExternalDeploy]] = []
    for name, identity in active.items():
        deploy = identity.get("deploy")
        if deploy and deploy.get("type") == "external":
            externals.append((name, cast(ExternalDeploy, deploy)))
    return externals
