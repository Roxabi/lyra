"""RED tests for --add-identity mode of scripts/gen_nkeys.py — #1361 T1-T4.

All tests are expected to FAIL until T5/T6 implementation lands:
  - argparse rejects --add-identity (unrecognized argument)
  - _mode_add_identity does not exist in _modes.py

Tests use:
  - NKEY_PROVIDER=fake + LYRA_TEST_MODE=1 (deterministic, no nk binary needed)
  - SEEDS_DIR=tmp_path fixture to avoid touching ~/.lyra/nkeys/
  - AUTH_DIR=tmp_path fixture to avoid /etc/nats/nkeys/ (bypasses _require_root)
  - A tiny 3-identity matrix created in tmp_path (hub, telegram-adapter, turn-writer)
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# ── tiny fixture matrix (3 identities) ───────────────────────────────────────

_TINY_MATRIX: dict = {
    "version": "2",
    "request_reply_flows": [],
    "identities": {
        "hub": {
            "status": "active",
            "created_at": "2026-01-01",
            "owner": "lyra",
            "description": "Hub core.",
            "allow_responses": False,
            "publish": ["lyra.outbound.>"],
            "subscribe": ["lyra.inbound.>"],
        },
        "telegram-adapter": {
            "status": "active",
            "created_at": "2026-01-01",
            "owner": "lyra",
            "description": "Telegram adapter.",
            "allow_responses": False,
            "publish": ["lyra.inbound.telegram.>"],
            "subscribe": ["lyra.outbound.telegram.>"],
        },
        "turn-writer": {
            "status": "active",
            "created_at": "2026-05-01",
            "owner": "lyra",
            "description": "Turn writer subscriber.",
            "allow_responses": False,
            "publish": ["lyra.turns.>"],
            "subscribe": ["lyra.turns.>"],
        },
    },
}

# Matrix with 'turn-writer' marked retired (for T3 retirement test)
_TINY_MATRIX_WITH_RETIRED: dict = {
    **_TINY_MATRIX,
    "identities": {
        **_TINY_MATRIX["identities"],
        "turn-writer": {
            **_TINY_MATRIX["identities"]["turn-writer"],
            "status": "retired",
            "retired_at": "2026-05-10",
        },
    },
}


# ── helpers ───────────────────────────────────────────────────────────────────


def _run_genkeys(
    args: list[str],
    *,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run gen_nkeys.py genkeys with given args via subprocess."""
    run_env = os.environ.copy()
    run_env["NKEY_PROVIDER"] = "fake"
    run_env["LYRA_TEST_MODE"] = "1"
    if env:
        run_env.update(env)
    return subprocess.run(
        [sys.executable, "scripts/gen_nkeys.py", "genkeys"] + args,
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=run_env,
        timeout=30,
    )


def _seed_bytes_for(name: str) -> bytes:
    """FakeNkeyProvider.gen_seed returns name.encode()."""
    return name.encode()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_fake_seeds(seeds_dir: Path, names: list[str]) -> None:
    seeds_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        seed_file = seeds_dir / f"{name}.seed"
        seed_file.write_bytes(_seed_bytes_for(name))
        seed_file.chmod(0o600)


def _write_matrix(tmp_path: Path, matrix: dict) -> Path:
    matrix_path = tmp_path / "acl-matrix.json"
    matrix_path.write_text(json.dumps(matrix))
    return matrix_path


# ── T1 — STATE=added (new identity full flow) ─────────────────────────────────


def test_add_identity_writes_only_new_seed(tmp_path: Path) -> None:
    """STATE=added: new seed created; existing seeds byte-identical; auth.conf complete.

    Spec trace: SC-1, SC-7.
    RED: --add-identity not wired yet → argparse error.
    """
    # Arrange
    seeds_dir = tmp_path / "nkeys"
    auth_dir = tmp_path / "etc-nats"
    auth_dir.mkdir(parents=True)
    matrix_path = _write_matrix(tmp_path, _TINY_MATRIX)

    # Pre-write seeds for existing identities (NOT for turn-writer — that's the new one)
    _write_fake_seeds(seeds_dir, ["hub", "telegram-adapter"])
    hub_sha = _sha256(seeds_dir / "hub.seed")
    telegram_sha = _sha256(seeds_dir / "telegram-adapter.seed")

    # Act
    result = _run_genkeys(
        ["--add-identity", "turn-writer", "--matrix", str(matrix_path)],
        env={"SEEDS_DIR": str(seeds_dir), "AUTH_DIR": str(auth_dir)},
    )

    # Assert — exit 0 and STATE=added on stdout
    assert result.returncode == 0, (
        f"Expected exit 0; got {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "STATE=added" in result.stdout, (
        f"Expected STATE=added in stdout; got: {result.stdout!r}"
    )

    # New seed must exist AND be 0o600 (review W2, #1363)
    new_seed = seeds_dir / "turn-writer.seed"
    assert new_seed.exists(), "turn-writer.seed must be created"
    assert new_seed.stat().st_mode & 0o777 == 0o600, (
        f"turn-writer.seed must be 0o600; got 0o{new_seed.stat().st_mode & 0o777:o}"
    )

    # Existing seeds must be byte-identical (SHA-256 unchanged)
    assert _sha256(seeds_dir / "hub.seed") == hub_sha, (
        "hub.seed was modified — must be untouched"
    )
    assert _sha256(seeds_dir / "telegram-adapter.seed") == telegram_sha, (
        "telegram-adapter.seed was modified — must be untouched"
    )

    # auth.conf must contain pubkey blocks for all three identities + be 0o600 (W2)
    auth_conf = seeds_dir / "auth.conf"
    assert auth_conf.exists(), "auth.conf must be written"
    assert auth_conf.stat().st_mode & 0o777 == 0o600, (
        f"auth.conf must be 0o600; got 0o{auth_conf.stat().st_mode & 0o777:o}"
    )
    content = auth_conf.read_text()
    assert "hub" in content, "auth.conf must contain hub block"
    assert "telegram-adapter" in content, (
        "auth.conf must contain telegram-adapter block"
    )
    assert "turn-writer" in content, "auth.conf must contain turn-writer block"


# ── T2 — STATE=noop AND STATE=repaired ────────────────────────────────────────


def test_add_identity_noop_when_full_consistency(tmp_path: Path) -> None:
    """STATE=noop: seed present AND auth.conf has block → no file mtime change.

    Spec trace: SC-2.
    RED: --add-identity not wired yet → argparse error.
    """
    # Arrange — all 3 seeds present + auth.conf already contains turn-writer block
    seeds_dir = tmp_path / "nkeys"
    auth_dir = tmp_path / "etc-nats"
    auth_dir.mkdir(parents=True)
    matrix_path = _write_matrix(tmp_path, _TINY_MATRIX)

    _write_fake_seeds(seeds_dir, ["hub", "telegram-adapter", "turn-writer"])

    # Write a real auth.conf via the actual renderer so parse_auth_conf sees
    # a structurally valid users block containing turn-writer (review B1).
    from scripts._loader import load_matrix
    from scripts._nk import FakeNkeyProvider
    from scripts._renderer import render_auth_conf

    matrix = load_matrix(matrix_path)
    provider = FakeNkeyProvider()
    pubkeys = {
        name: provider.pubkey_from_seed((seeds_dir / f"{name}.seed").read_bytes())
        for name in ("hub", "telegram-adapter", "turn-writer")
    }
    auth_conf = seeds_dir / "auth.conf"
    auth_conf.write_text(render_auth_conf(matrix, pubkeys))
    auth_conf.chmod(0o600)

    # Capture mtimes before invocation
    time.sleep(0.01)  # ensure mtime granularity
    seeds_mtime_before = {f.name: f.stat().st_mtime for f in seeds_dir.iterdir()}

    # Act
    result = _run_genkeys(
        ["--add-identity", "turn-writer", "--matrix", str(matrix_path)],
        env={"SEEDS_DIR": str(seeds_dir), "AUTH_DIR": str(auth_dir)},
    )

    # Assert — exit 0, STATE=noop, NO mtime changes
    assert result.returncode == 0, (
        f"Expected exit 0; got {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "STATE=noop" in result.stdout, (
        f"Expected STATE=noop in stdout; got: {result.stdout!r}"
    )

    # No file in seeds_dir must have a newer mtime
    for fname, mtime_before in seeds_mtime_before.items():
        f = seeds_dir / fname
        if f.exists():
            assert f.stat().st_mtime == mtime_before, (
                f"{fname} mtime changed — noop must leave disk untouched"
            )


def test_add_identity_repairs_when_seed_present_but_block_missing(
    tmp_path: Path,
) -> None:
    """STATE=repaired: seed exists, auth.conf missing block → rewrite; seed unchanged.

    Spec trace: SC-3.
    RED: --add-identity not wired yet → argparse error.
    """
    # Arrange — all 3 seeds present BUT auth.conf does NOT contain turn-writer block
    seeds_dir = tmp_path / "nkeys"
    auth_dir = tmp_path / "etc-nats"
    auth_dir.mkdir(parents=True)
    matrix_path = _write_matrix(tmp_path, _TINY_MATRIX)

    _write_fake_seeds(seeds_dir, ["hub", "telegram-adapter", "turn-writer"])

    # Partial auth.conf — missing turn-writer block
    auth_conf = seeds_dir / "auth.conf"
    auth_conf.write_text(
        "authorization {\n"
        "  # hub\n"
        "  # telegram-adapter\n"
        "  (no turn-writer block here)\n"
        "}\n"
    )
    auth_conf.chmod(0o600)

    seed_mtime_before = (seeds_dir / "turn-writer.seed").stat().st_mtime
    auth_conf_mtime_before = auth_conf.stat().st_mtime
    time.sleep(0.02)  # ensure mtime granularity for auth.conf rewrite detection

    # Act
    result = _run_genkeys(
        ["--add-identity", "turn-writer", "--matrix", str(matrix_path)],
        env={"SEEDS_DIR": str(seeds_dir), "AUTH_DIR": str(auth_dir)},
    )

    # Assert — exit 0, STATE=repaired
    assert result.returncode == 0, (
        f"Expected exit 0; got {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "STATE=repaired" in result.stdout, (
        f"Expected STATE=repaired in stdout; got: {result.stdout!r}"
    )

    # auth.conf mtime must have advanced past its own pre-run mtime (rewrite happened).
    # The prior `> seed_mtime_before - 0.1` was tautological (review W1, #1363).
    assert auth_conf.stat().st_mtime > auth_conf_mtime_before, (
        "auth.conf must be rewritten in STATE=repaired"
    )

    # turn-writer.seed mtime must be unchanged
    assert (seeds_dir / "turn-writer.seed").stat().st_mtime == seed_mtime_before, (
        "turn-writer.seed must not be touched in STATE=repaired (seed already exists)"
    )

    # auth.conf must now contain the turn-writer block
    content = auth_conf.read_text()
    assert "turn-writer" in content, (
        "auth.conf must contain turn-writer block after STATE=repaired"
    )


# ── T3 — validation errors ────────────────────────────────────────────────────


def test_add_identity_unknown_name_in_matrix_fails(tmp_path: Path) -> None:
    """Identity not in matrix → non-zero exit, no seed created, stderr names the matrix.

    Spec trace: SC-4.
    RED: --add-identity not wired yet → argparse error.
    """
    # Arrange
    seeds_dir = tmp_path / "nkeys"
    auth_dir = tmp_path / "etc-nats"
    auth_dir.mkdir(parents=True)
    matrix_path = _write_matrix(tmp_path, _TINY_MATRIX)
    _write_fake_seeds(seeds_dir, ["hub", "telegram-adapter"])

    # Act — request an identity that does not exist in the matrix
    result = _run_genkeys(
        ["--add-identity", "ghost-identity", "--matrix", str(matrix_path)],
        env={"SEEDS_DIR": str(seeds_dir), "AUTH_DIR": str(auth_dir)},
    )

    # Assert — non-zero, no seed file, stderr references the matrix
    assert result.returncode != 0, (
        f"Expected non-zero exit for unknown identity; got {result.returncode}"
    )
    ghost_seed = seeds_dir / "ghost-identity.seed"
    assert not ghost_seed.exists(), "No seed file must be created for unknown identity"
    # stderr must mention the matrix file (file name or path)
    assert "acl-matrix" in result.stderr or str(matrix_path) in result.stderr, (
        f"stderr must reference the matrix file; got: {result.stderr!r}"
    )


def test_add_identity_retired_identity_fails(tmp_path: Path) -> None:
    """Identity status=retired → non-zero exit, no seed created, stderr mentions status.

    Spec trace: SC-5.
    RED: --add-identity not wired yet → argparse error.
    """
    # Arrange — matrix has turn-writer as retired
    seeds_dir = tmp_path / "nkeys"
    auth_dir = tmp_path / "etc-nats"
    auth_dir.mkdir(parents=True)
    matrix_path = _write_matrix(tmp_path, _TINY_MATRIX_WITH_RETIRED)
    _write_fake_seeds(seeds_dir, ["hub", "telegram-adapter"])

    # Act
    result = _run_genkeys(
        ["--add-identity", "turn-writer", "--matrix", str(matrix_path)],
        env={"SEEDS_DIR": str(seeds_dir), "AUTH_DIR": str(auth_dir)},
    )

    # Assert — non-zero, no seed, stderr mentions the status
    assert result.returncode != 0, (
        f"Expected non-zero exit for retired identity; got {result.returncode}"
    )
    retired_seed = seeds_dir / "turn-writer.seed"
    assert not retired_seed.exists(), (
        "No seed file must be created for retired identity"
    )
    assert "retired" in result.stderr or "status" in result.stderr, (
        f"stderr must mention the identity status; got: {result.stderr!r}"
    )


def test_add_identity_missing_other_seed_fails_no_orphan(tmp_path: Path) -> None:
    """Other active seed missing → non-zero exit before any write; no orphan new seed.

    Matrix: hub (active) + telegram-adapter (active) + turn-writer (active, NEW).
    Pre-state: only hub.seed exists (telegram-adapter.seed missing).
    Expected: non-zero, turn-writer.seed NOT created, stderr names missing identity.

    Spec trace: SC-6.
    RED: --add-identity not wired yet → argparse error.
    """
    # Arrange — only hub seed present; telegram-adapter missing; turn-writer is new
    seeds_dir = tmp_path / "nkeys"
    auth_dir = tmp_path / "etc-nats"
    auth_dir.mkdir(parents=True)
    matrix_path = _write_matrix(tmp_path, _TINY_MATRIX)
    _write_fake_seeds(seeds_dir, ["hub"])  # telegram-adapter deliberately absent

    # Act
    result = _run_genkeys(
        ["--add-identity", "turn-writer", "--matrix", str(matrix_path)],
        env={"SEEDS_DIR": str(seeds_dir), "AUTH_DIR": str(auth_dir)},
    )

    # Assert — exact exit 1 (controlled sys.exit, not a crash).
    # `!= 0` would also accept a Python traceback exit (W4, #1363).
    assert result.returncode == 1, (
        f"Expected exit 1 (controlled validation failure); got {result.returncode}\n"
        f"stderr: {result.stderr}"
    )
    new_seed = seeds_dir / "turn-writer.seed"
    assert not new_seed.exists(), (
        "turn-writer.seed must NOT be created (no orphan) when another seed is missing"
    )
    assert "telegram-adapter" in result.stderr, (
        "stderr must name the missing identity 'telegram-adapter';"
        f" got: {result.stderr!r}"
    )


# ── T4 — rootless invariants ──────────────────────────────────────────────────


def test_add_identity_runs_as_non_root(tmp_path: Path) -> None:
    """--add-identity succeeds without sudo elevation (rootless mode).

    The subprocess must NOT fail with 'must be run as root'.
    Note: currently fails with 'unrecognized argument' because --add-identity is not
    wired yet — that is the expected RED failure mode.

    Spec trace: SC-9.
    RED: --add-identity not wired yet → argparse error (NOT a root error).
    """
    # Precondition: must run as non-root, otherwise this test is tautological —
    # a buggy mode calling _require_root() would still exit 0 under root (W3, #1363).
    assert os.getuid() != 0, "this test requires a non-root user"

    # Arrange
    seeds_dir = tmp_path / "nkeys"
    auth_dir = tmp_path / "etc-nats"
    auth_dir.mkdir(parents=True)
    matrix_path = _write_matrix(tmp_path, _TINY_MATRIX)
    _write_fake_seeds(seeds_dir, ["hub", "telegram-adapter"])

    # Act — run without any sudo, as current user
    result = _run_genkeys(
        ["--add-identity", "turn-writer", "--matrix", str(matrix_path)],
        env={"SEEDS_DIR": str(seeds_dir), "AUTH_DIR": str(auth_dir)},
    )

    # Assert — must exit 0 (rootless) and NOT fail with root-required message
    assert result.returncode == 0, (
        f"Expected exit 0 for rootless --add-identity; got {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "must be run as root" not in result.stderr, (
        "--add-identity must NOT require root; got root error in stderr"
    )


def test_add_identity_does_not_touch_system_path(tmp_path: Path) -> None:
    """--add-identity writes ONLY to SEEDS_DIR/auth.conf; AUTH_DIR files are untouched.

    Setup: AUTH_DIR has a pre-existing auth.conf with known mtime.
    After invocation: every file in AUTH_DIR must have the same mtime as before.

    Spec trace: SC-8.
    RED: --add-identity not wired yet → argparse error.
    """
    # Arrange
    seeds_dir = tmp_path / "nkeys"
    auth_dir = tmp_path / "etc-nats-mirror"
    auth_dir.mkdir(parents=True)
    matrix_path = _write_matrix(tmp_path, _TINY_MATRIX)
    _write_fake_seeds(seeds_dir, ["hub", "telegram-adapter"])

    # Create a file in AUTH_DIR to serve as the system-path sentinel
    system_auth_conf = auth_dir / "auth.conf"
    system_auth_conf.write_text(
        "authorization { # existing system auth.conf — must not be touched }\n"
    )
    system_auth_conf.chmod(0o640)

    time.sleep(0.02)  # ensure mtime granularity
    auth_dir_mtimes_before = {f.name: f.stat().st_mtime for f in auth_dir.iterdir()}

    # Act
    result = _run_genkeys(
        ["--add-identity", "turn-writer", "--matrix", str(matrix_path)],
        env={"SEEDS_DIR": str(seeds_dir), "AUTH_DIR": str(auth_dir)},
    )

    # Assert — invocation must succeed (exit 0)
    assert result.returncode == 0, (
        f"Expected exit 0; got {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    # Every file in AUTH_DIR must have the same mtime (none touched)
    for fname, mtime_before in auth_dir_mtimes_before.items():
        f = auth_dir / fname
        if f.exists():
            assert f.stat().st_mtime == mtime_before, (
                f"AUTH_DIR/{fname} mtime changed — system path must not be touched"
            )

    # New files must NOT have been created in AUTH_DIR
    auth_dir_files_after = {f.name for f in auth_dir.iterdir()}
    auth_dir_files_before = set(auth_dir_mtimes_before.keys())
    new_files = auth_dir_files_after - auth_dir_files_before
    assert not new_files, f"New files created in AUTH_DIR (system path): {new_files}"
