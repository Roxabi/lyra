"""Mode implementations for gen_nkeys.py — #1017 Slice 2 helpers + provisioning."""

from __future__ import annotations

import argparse
import grp
import os
import pwd
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Callable

from scripts._acl_models import LoadedMatrix
from scripts._loader import load_matrix
from scripts._nk import (
    FakeNkeyProvider,
    NkeyProvider,
    SubprocessNkeyProvider,
    ensure_nk_or_exit,
)
from scripts._renderer import parse_auth_conf, render_auth_conf

_provider_factory: Callable[[], NkeyProvider] = SubprocessNkeyProvider


def _get_provider() -> NkeyProvider:
    """Return the active NkeyProvider; exit 1 with install hint when nk is absent."""
    env_provider = os.environ.get("NKEY_PROVIDER", "").lower()
    if env_provider == "fake":
        if not os.environ.get("LYRA_TEST_MODE"):
            print(
                "error: NKEY_PROVIDER=fake requires LYRA_TEST_MODE=1"
                " — refusing to generate fake seeds outside test context",
                file=sys.stderr,
            )
            sys.exit(1)
        return FakeNkeyProvider()
    ensure_nk_or_exit()
    return _provider_factory()


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


def _require_root() -> None:
    """Exit 1 unless running as root. Skipped when AUTH_DIR env var is set (tests)."""
    if os.environ.get("AUTH_DIR"):
        return  # test override: AUTH_DIR redirects system paths — no root needed
    if os.geteuid() != 0:
        print("error: must be run as root (sudo lyra-acl genkeys ...)", file=sys.stderr)
        sys.exit(1)


def _operator_uid_gid() -> tuple[int, int]:
    """Return (uid, gid) of the operator (SUDO_USER if set, else current user)."""
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        pw = pwd.getpwnam(sudo_user)
        return pw.pw_uid, pw.pw_gid
    return os.getuid(), os.getgid()


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
                f"error: missing seed: {seed_file} — run 'uv run lyra-acl genkeys'",
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
                f"error: missing lyra seed: {seed_file}"
                " — run 'uv run lyra-acl genkeys'",
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
    import time

    epoch = int(time.time())
    backup_auth: str | None = None
    backup_seeds: str | None = None

    if auth_conf.exists():
        backup_auth = str(auth_conf) + f".bak.{epoch}"
        shutil.copy2(str(auth_conf), backup_auth)
    if seeds_dir.exists():
        backup_seeds = str(seeds_dir) + f".bak.{epoch}"
        shutil.copytree(str(seeds_dir), backup_seeds)
        shutil.rmtree(str(seeds_dir))
    if auth_conf.exists():
        auth_conf.unlink()

    try:
        _mode_full_provision(args)
    except BaseException:
        if backup_seeds and Path(backup_seeds).exists() and not seeds_dir.exists():
            shutil.copytree(backup_seeds, str(seeds_dir))
        if backup_auth and Path(backup_auth).exists() and not auth_conf.exists():
            shutil.copy2(backup_auth, str(auth_conf))
        raise


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
    """--fix-perms: re-apply permissions and ownership (root required)."""
    _require_root()
    seeds_dir = _seeds_dir()
    auth_dir = _auth_dir()
    auth_conf = auth_dir / "auth.conf"
    uid, gid = _operator_uid_gid()

    if seeds_dir.exists():
        seeds_dir.chmod(0o700)
        os.chown(seeds_dir, uid, gid)
        for seed_file in seeds_dir.glob("*.seed"):
            seed_file.chmod(0o600)
            os.chown(seed_file, uid, gid)

    if auth_conf.exists():
        auth_conf.chmod(0o640)
        if not os.environ.get("AUTH_DIR"):
            nats_gid = grp.getgrnam("nats").gr_gid
            os.chown(auth_conf, 0, nats_gid)


def _mode_full_provision(args: argparse.Namespace) -> None:
    """Default mode: generate all nkeys + dual-write auth.conf (root required)."""
    _require_root()
    seeds_dir = _seeds_dir()
    auth_dir = _auth_dir()
    matrix = load_matrix(args.matrix)
    provider = _get_provider()
    uid, gid = _operator_uid_gid()

    seeds_dir.mkdir(parents=True, exist_ok=True)
    seeds_dir.chmod(0o700)
    os.chown(seeds_dir, uid, gid)
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

    # System write: /etc/nats/nkeys/auth.conf (0640, root:nats)
    system_conf = auth_dir / "auth.conf"
    atomic_write(system_conf, content, 0o640)
    if not os.environ.get("AUTH_DIR"):
        nats_gid = grp.getgrnam("nats").gr_gid
        os.chown(system_conf, 0, nats_gid)

    # User mirror: ~/.lyra/nkeys/auth.conf (0600, operator-owned)
    user_conf = seeds_dir / "auth.conf"
    atomic_write(user_conf, content, 0o600)
    os.chown(user_conf, uid, gid)
