"""RED tests for key-aware modes of scripts/gen_nkeys.py — #1017 T19.

These tests document the expected behaviour of the V2 write modes
(--regen-authconf, --emit-merged-authconf, --regenerate, --show).

All tests FAIL in Slice 1 because every mode beyond --template-only and
--validate-supervisor raises SystemExit("not yet implemented in this slice").
They will turn GREEN when T24 (Slice 2 implementation) lands.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_MATRIX_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "v2-prod.json"


# ── helpers ───────────────────────────────────────────────────────────────────


def _run_genkeys(
    args: list[str],
    *,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run gen_nkeys.py genkeys with given args via subprocess."""
    run_env = os.environ.copy()
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
    """Return fake (non-real nkey) seed bytes for a named identity.

    FakeNkeyProvider.gen_seed returns name.encode() — mirror that here
    so seed files set up by tests are consistent with the provider.
    """
    return name.encode()


def _write_fake_seeds(seeds_dir: Path, identities: list[str]) -> None:
    """Write fake seed files for the given identity names into seeds_dir."""
    seeds_dir.mkdir(parents=True, exist_ok=True)
    for name in identities:
        seed_file = seeds_dir / f"{name}.seed"
        seed_file.write_bytes(_seed_bytes_for(name))
        seed_file.chmod(0o600)


# ── T19.1 — --regen-authconf exits cleanly ───────────────────────────────────


class TestRegenAuthconf:
    def test_regen_authconf_exits_cleanly(self, tmp_path: Path) -> None:
        """--regen-authconf exits 0 and writes an auth.conf when seeds exist.

        SC-2: --regen-authconf reads existing seeds, derives pubkeys via
        NkeyProvider, and writes ~/.lyra/nkeys/auth.conf with mode 0600.
        Will FAIL now: mode raises SystemExit("not yet implemented in this slice").
        """
        # Arrange — write fake seeds for all identities in the v2-prod fixture
        seeds_dir = tmp_path / "nkeys"
        matrix_data = json.loads(_MATRIX_FIXTURE.read_text())
        active_identities = [
            name
            for name, ident in matrix_data["identities"].items()
            if ident["status"] == "active"
        ]
        _write_fake_seeds(seeds_dir, active_identities)

        # Act
        result = _run_genkeys(
            [
                "--regen-authconf",
                "--matrix",
                str(_MATRIX_FIXTURE),
            ],
            env={"SEEDS_DIR": str(seeds_dir)},
        )

        # Assert
        assert result.returncode == 0, (
            f"Expected exit 0; got {result.returncode}\n"
            f"stderr: {result.stderr}"
        )
        assert "not yet implemented" not in result.stderr

    def test_regen_authconf_writes_user_auth_conf(self, tmp_path: Path) -> None:
        """--regen-authconf writes auth.conf into the seeds directory.

        SC-2: the output file must exist after the call and must contain
        'authorization {' (top-level block marker).
        Will FAIL now: mode raises SystemExit("not yet implemented in this slice").
        """
        # Arrange
        seeds_dir = tmp_path / "nkeys"
        matrix_data = json.loads(_MATRIX_FIXTURE.read_text())
        active_identities = [
            name
            for name, ident in matrix_data["identities"].items()
            if ident["status"] == "active"
        ]
        _write_fake_seeds(seeds_dir, active_identities)

        # Act
        result = _run_genkeys(
            [
                "--regen-authconf",
                "--matrix",
                str(_MATRIX_FIXTURE),
            ],
            env={"SEEDS_DIR": str(seeds_dir)},
        )

        # Assert — file must exist and carry the auth block
        auth_conf = seeds_dir / "auth.conf"
        assert result.returncode == 0, (
            f"Expected exit 0; got {result.returncode}\n"
            f"stderr: {result.stderr}"
        )
        assert auth_conf.exists(), "auth.conf must be written by --regen-authconf"
        content = auth_conf.read_text()
        assert "authorization" in content, "auth.conf must contain 'authorization' block"


# ── T19.3 — --emit-merged-authconf ───────────────────────────────────────────


class TestEmitMergedAuthconf:
    def test_emit_merged_authconf_to_stdout(self, tmp_path: Path) -> None:
        """--emit-merged-authconf writes merged auth.conf covering active identities.

        SC-5: emit-merged-authconf reads existing seeds for all active identities
        (lyra + voicecli owners), derives pubkeys, and writes ~/.lyra/nkeys/auth.conf.
        Stdout or the written file must reference the lyra identities (hub,
        clipool-worker) and voicecli identities (voice-tts).
        Will FAIL now: mode raises SystemExit("not yet implemented in this slice").
        """
        # Arrange — seeds for all identities in v2-prod
        seeds_dir = tmp_path / "nkeys"
        voicecli_seeds_dir = tmp_path / "voicecli_nkeys"
        matrix_data = json.loads(_MATRIX_FIXTURE.read_text())
        lyra_identities = [
            name
            for name, ident in matrix_data["identities"].items()
            if ident["status"] == "active" and ident["owner"] == "lyra"
        ]
        voicecli_identities = [
            name
            for name, ident in matrix_data["identities"].items()
            if ident["status"] == "active" and ident["owner"] == "voicecli"
        ]
        _write_fake_seeds(seeds_dir, lyra_identities)
        _write_fake_seeds(voicecli_seeds_dir, voicecli_identities)

        # Act
        result = _run_genkeys(
            [
                "--emit-merged-authconf",
                "--matrix",
                str(_MATRIX_FIXTURE),
            ],
            env={
                "SEEDS_DIR": str(seeds_dir),
                "VOICECLI_SEEDS_DIR": str(voicecli_seeds_dir),
            },
        )

        # Assert
        assert result.returncode == 0, (
            f"Expected exit 0; got {result.returncode}\n"
            f"stderr: {result.stderr}"
        )
        auth_conf = seeds_dir / "auth.conf"
        assert auth_conf.exists(), "auth.conf must be written by --emit-merged-authconf"
        content = auth_conf.read_text()
        assert "authorization" in content
        # v2-prod fixture has hub (lyra) and voice-tts (voicecli)
        assert "hub" in content
        assert "voice-tts" in content


# ── T19.4 — --regenerate requires root or --yes ──────────────────────────────


class TestRegenerateMode:
    def test_regenerate_requires_root_or_yes(self) -> None:
        """--regenerate without root and without --yes must exit 1 with an error.

        SC-6: --regenerate is a destructive operation; it requires either
        running as root or explicit --yes confirmation. When neither condition
        is met and stdin is not a TTY (subprocess), it must fail fast.
        Will FAIL now: mode raises SystemExit("not yet implemented in this slice").
        """
        # Act — run as non-root (current user in CI), no --yes flag, no TTY
        result = _run_genkeys(
            [
                "--regenerate",
                "--matrix",
                str(_MATRIX_FIXTURE),
            ]
        )

        # Assert — must exit non-zero with an informative message
        # (either "not root" or "stdin is not a TTY" style error)
        assert result.returncode != 0, (
            "Expected non-zero exit for --regenerate without root/--yes; "
            f"got {result.returncode}"
        )
        # Once implemented, "not yet implemented" must not appear
        assert "not yet implemented" not in result.stderr, (
            "--regenerate must be implemented and produce a real error, "
            "not a stub message"
        )

    def test_regenerate_with_yes_requires_root(self) -> None:
        """--regenerate --yes without root must exit 1 with a root-required message.

        SC-6: even with --yes, full provisioning (seed generation + system write)
        requires root because it writes to /etc/nats/nkeys/.
        Will FAIL now: mode raises SystemExit("not yet implemented in this slice").
        """
        # Act — non-root, --yes skips TTY check but root check must still fire
        result = _run_genkeys(
            [
                "--regenerate",
                "--yes",
                "--matrix",
                str(_MATRIX_FIXTURE),
            ]
        )

        # Assert
        assert result.returncode != 0, (
            "Expected non-zero exit for --regenerate --yes without root; "
            f"got {result.returncode}"
        )
        assert "not yet implemented" not in result.stderr


# ── T19.5 — --show requires root ─────────────────────────────────────────────


class TestShowMode:
    def test_show_requires_root(self) -> None:
        """--show without root must exit 1 with a root-required message.

        SC-6: --show reads /etc/nats/nkeys/auth.conf which is root:nats 0640.
        A non-root user cannot read it; the CLI must check and exit 1.
        Will FAIL now: mode raises SystemExit("not yet implemented in this slice").
        """
        # Act
        result = _run_genkeys(
            [
                "--show",
                "--matrix",
                str(_MATRIX_FIXTURE),
            ]
        )

        # Assert
        assert result.returncode != 0, (
            "Expected non-zero exit for --show without root; "
            f"got {result.returncode}"
        )
        assert "not yet implemented" not in result.stderr


# ── T19.6 — default mode dual-write ──────────────────────────────────────────


class TestDefaultModeDualWrite:
    def test_default_mode_dual_write(self, tmp_path: Path) -> None:
        """Default mode (no flag) writes system auth.conf AND user auth.conf.

        SC-2 / spec default-mode-dual-write: the full provisioning path writes
        /etc/nats/nkeys/auth.conf (system, 0640, root:nats) AND mirrors a copy
        to ~/.lyra/nkeys/auth.conf (user, 0600, operator).
        This test uses tmp_path overrides for both paths.
        Will FAIL now: mode raises SystemExit("not yet implemented in this slice").
        """
        # Arrange — tmp directories for both write targets
        system_auth_dir = tmp_path / "etc_nats_nkeys"
        system_auth_dir.mkdir(parents=True)
        user_seeds_dir = tmp_path / "user_nkeys"
        user_seeds_dir.mkdir(parents=True)

        # Act
        result = _run_genkeys(
            [
                "--matrix",
                str(_MATRIX_FIXTURE),
            ],
            env={
                "SEEDS_DIR": str(user_seeds_dir),
                "AUTH_DIR": str(system_auth_dir),
            },
        )

        # Assert — both files must exist after the call
        system_conf = system_auth_dir / "auth.conf"
        user_conf = user_seeds_dir / "auth.conf"

        assert result.returncode == 0, (
            f"Expected exit 0 for default mode; got {result.returncode}\n"
            f"stderr: {result.stderr}"
        )
        assert system_conf.exists(), (
            "System auth.conf must be written to AUTH_DIR by default mode"
        )
        assert user_conf.exists(), (
            "User auth.conf must be mirrored to SEEDS_DIR by default mode"
        )
        assert "not yet implemented" not in result.stderr
