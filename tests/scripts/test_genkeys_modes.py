"""RED tests for key-aware modes of scripts/gen_nkeys.py — #1017 T19.

These tests document the expected behaviour of the V2 write modes
(--regen-authconf, --emit-merged-authconf, --regenerate, --show).

All tests FAIL in Slice 1 because every mode beyond --template-only raises
SystemExit("not yet implemented in this slice").
They will turn GREEN when T24 (Slice 2 implementation) lands.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import scripts._modes as _modes

from tests.fakes.nkey_provider import FakeNkeyProvider

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
        [sys.executable, "tests/scripts/_fake_genkeys.py", "genkeys"] + args,
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
            f"Expected exit 0; got {result.returncode}\nstderr: {result.stderr}"
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
            f"Expected exit 0; got {result.returncode}\nstderr: {result.stderr}"
        )
        assert auth_conf.exists(), "auth.conf must be written by --regen-authconf"
        content = auth_conf.read_text()
        assert "authorization" in content, (
            "auth.conf must contain 'authorization' block"
        )


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
            f"Expected exit 0; got {result.returncode}\nstderr: {result.stderr}"
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

    def test_regenerate_restores_on_provision_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """seeds_dir is restored from backup if _mode_full_provision raises."""
        import argparse
        from unittest.mock import patch

        from scripts._modes import _mode_regenerate

        # Arrange: seeds_dir and auth_dir in tmp_path
        seeds_dir = tmp_path / "nkeys"
        auth_dir = tmp_path / "auth"
        auth_dir.mkdir()
        seeds_dir.mkdir()
        (seeds_dir / "hub.seed").write_bytes(b"original-seed")
        (auth_dir / "auth.conf").write_text("original-auth-conf")

        # Use env overrides so _require_root is skipped and paths redirect to tmp_path
        monkeypatch.setenv("SEEDS_DIR", str(seeds_dir))
        monkeypatch.setenv("AUTH_DIR", str(auth_dir))

        args = argparse.Namespace(yes=True, matrix=_MATRIX_FIXTURE)

        # Patch _mode_full_provision to raise after seeds are wiped
        with patch(
            "scripts._modes._mode_full_provision",
            side_effect=RuntimeError("simulated provision failure"),
        ):
            with pytest.raises(RuntimeError, match="simulated provision failure"):
                _mode_regenerate(args)

        # After failed provision: seeds_dir must be restored from backup
        assert seeds_dir.exists(), (
            "seeds_dir must be restored from backup after _mode_full_provision failure"
        )
        assert (seeds_dir / "hub.seed").read_bytes() == b"original-seed", (
            "Seed file content must match the pre-wipe backup"
        )


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
            f"Expected non-zero exit for --show without root; got {result.returncode}"
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


# ── Regression tests for #1089: rendered nkey lengths ────────────────────────


class TestRenderedNkeyLength:
    """Regression tests for #1089 — auth.conf nkeys must be exactly 56 chars.

    Before the fix, FakeNkeyProvider.pubkey_from_seed returned UDET+seed_content
    (62 chars for real seeds), which was rejected by nats-server as an invalid
    public nkey for a user.
    """

    def _active_identities(self) -> list[str]:
        matrix_data = json.loads(_MATRIX_FIXTURE.read_text())
        return [
            name
            for name, ident in matrix_data["identities"].items()
            if ident["status"] == "active"
        ]

    def _render_auth_conf(self, seeds_dir: "Path") -> str:
        """Run --regen-authconf and return the rendered auth.conf text."""
        result = _run_genkeys(
            ["--regen-authconf", "--matrix", str(_MATRIX_FIXTURE)],
            env={"SEEDS_DIR": str(seeds_dir)},
        )
        assert result.returncode == 0, f"--regen-authconf failed: {result.stderr}"
        return (seeds_dir / "auth.conf").read_text()

    def test_regen_authconf_all_nkeys_are_56_chars(self, tmp_path: "Path") -> None:
        """Every nkey in rendered auth.conf must be exactly 56 chars.

        Regression: #1089 — FakeNkeyProvider produced 62-char malformed nkeys
        (UDET + raw seed content) that were rejected by nats-server.
        """
        import re

        seeds_dir = tmp_path / "nkeys"
        _write_fake_seeds(seeds_dir, self._active_identities())
        content = self._render_auth_conf(seeds_dir)

        nkey_values = re.findall(r'nkey\s*:\s*"([^"]*)"', content)
        assert nkey_values, "auth.conf must contain at least one nkey entry"
        for nkey in nkey_values:
            assert len(nkey) == 56, (
                f"nkey must be exactly 56 chars; got {len(nkey)}: {nkey!r}. "
                f"Likely regression: UDET+seed concatenation (#1089)."
            )

    def test_regen_authconf_nkeys_start_with_u(self, tmp_path: "Path") -> None:
        """Every nkey in rendered auth.conf must start with U (NATS user prefix)."""
        import re

        seeds_dir = tmp_path / "nkeys"
        _write_fake_seeds(seeds_dir, self._active_identities())
        content = self._render_auth_conf(seeds_dir)

        nkey_values = re.findall(r'nkey\s*:\s*"([^"]*)"', content)
        assert nkey_values, "auth.conf must contain at least one nkey entry"
        for nkey in nkey_values:
            assert nkey.startswith("U"), (
                f"nkey must start with U (NATS user prefix); got: {nkey!r}"
            )

    def test_regen_authconf_no_embedded_newlines_in_nkey_strings(
        self, tmp_path: "Path"
    ) -> None:
        """Rendered auth.conf must not have newlines inside nkey quoted strings.

        Secondary regression from #1089: the closing quote appeared on its own
        line when nkey values contained newlines.
        """
        seeds_dir = tmp_path / "nkeys"
        _write_fake_seeds(seeds_dir, self._active_identities())
        content = self._render_auth_conf(seeds_dir)

        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("nkey:"):
                assert stripped.startswith('nkey: "') and stripped.endswith('"'), (
                    f"nkey line must open and close quote on same line; got: {line!r}"
                )


# ── T6 — #1379 Slice V2: external seed fail-loud tests ───────────────────────


def _make_matrix(
    tmp_path: Path,
    *,
    with_external: bool,
    external_name: str = "voice-client",
    external_host: str = "roxabitower",
    external_target_path: str = "~/.voicecli/nkeys/voice-client.seed",
) -> Path:
    """Write a minimal acl-matrix.json to tmp_path and return its Path.

    When with_external=True, adds one external identity (voice-client) alongside
    two container identities (hub, clipool-worker).  When False, only container
    identities are present — no externals.
    """
    identities: dict = {
        "hub": {
            "status": "active",
            "created_at": "2026-01-01",
            "owner": "lyra",
            "description": "hub",
            "allow_responses": False,
            "publish": ["lyra.out.>"],
            "subscribe": ["lyra.in.>"],
            "deploy": {"type": "container", "secret": "lyra-nats-hub"},
        },
        "clipool-worker": {
            "status": "active",
            "created_at": "2026-01-01",
            "owner": "lyra",
            "description": "clipool worker",
            "allow_responses": True,
            "publish": ["lyra.clipool.heartbeat"],
            "subscribe": ["lyra.clipool.cmd"],
            "deploy": {"type": "container", "secret": "lyra-nats-clipool"},
        },
    }
    if with_external:
        identities[external_name] = {
            "status": "active",
            "created_at": "2026-01-01",
            "owner": "voicecli",
            "description": "voice client on M2",
            "allow_responses": False,
            "publish": ["lyra.voice.>"],
            "subscribe": ["lyra.voice.response.>"],
            "deploy": {
                "type": "external",
                "host": external_host,
                "target_path": external_target_path,
            },
        }
    matrix_path = tmp_path / "matrix.json"
    matrix_path.write_text(
        json.dumps({"version": "2", "identities": identities}), encoding="utf-8"
    )
    return matrix_path


class TestExternalFailLoud:
    """#1379 Slice V2 — _mode_full_provision fail-loud on external identities."""

    def test_full_provision_returns_externals_list(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_mode_full_provision returns the right externals list for both shapes.

        SC-7: when zero externals exist → []; when ≥1 exists → list[(name, deploy)]
        for each. Asserting BOTH paths catches the deletion of the collection
        loop (a no-loop function returns [] in the no-externals case but also
        returns [] in the with-externals case — the empty-only assertion is
        tautological).

        verified: removing the `if deploy.get("type") == "external"` branch
        causes the with-external assertion to fail (empty list vs expected 1).
        """
        import argparse

        from scripts._modes import _mode_full_provision

        seeds_dir = tmp_path / "nkeys"
        auth_dir = tmp_path / "auth"
        auth_dir.mkdir(parents=True)
        monkeypatch.setenv("SEEDS_DIR", str(seeds_dir))
        monkeypatch.setenv("AUTH_DIR", str(auth_dir))
        monkeypatch.setattr(_modes, "_provider_factory", FakeNkeyProvider)

        # No externals → []
        matrix_no_ext = _make_matrix(tmp_path, with_external=False)
        args_no = argparse.Namespace(
            matrix=matrix_no_ext, yes=True, ack_external_distribution=False
        )
        assert _mode_full_provision(args_no) == [], (
            "_mode_full_provision must return [] when no external identities exist"
        )

        # With externals → exactly the external entry, with correct shape
        seeds_dir2 = tmp_path / "nkeys2"
        auth_dir2 = tmp_path / "auth2"
        auth_dir2.mkdir(parents=True)
        monkeypatch.setenv("SEEDS_DIR", str(seeds_dir2))
        monkeypatch.setenv("AUTH_DIR", str(auth_dir2))

        matrix_with_ext = _make_matrix(tmp_path, with_external=True)
        args_yes = argparse.Namespace(
            matrix=matrix_with_ext, yes=True, ack_external_distribution=True
        )
        externals = _mode_full_provision(args_yes)
        assert len(externals) == 1, (
            f"with_external=True matrix has exactly one external identity;"
            f" _mode_full_provision returned {externals!r}"
        )
        name, deploy = externals[0]
        assert name == "voice-client", (
            f"expected the single external to be 'voice-client'; got '{name}'"
        )
        assert deploy["type"] == "external"
        assert "host" in deploy and "target_path" in deploy

    def test_full_provision_externals_no_ack_exits_2(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """Full provisioning exits 2 when externals present and --ack flag absent.

        SC-8/SC-9: seeds and auth.conf are written first, then the caller
        emits the manifest and exits 2 (sentinel "regen done, manual action required").
        RED: external-detection, manifest emission, and exit-2 logic don't exist yet.
        Will turn GREEN when T7/T8/T9 land.
        """
        seeds_dir = tmp_path / "nkeys"
        auth_dir = tmp_path / "auth"
        auth_dir.mkdir(parents=True)
        matrix_path = _make_matrix(tmp_path, with_external=True)

        result = _run_genkeys(
            [
                "--regenerate",
                "--yes",
                "--matrix",
                str(matrix_path),
            ],
            env={
                "SEEDS_DIR": str(seeds_dir),
                "AUTH_DIR": str(auth_dir),
            },
        )

        # Assert exit 2
        assert result.returncode == 2, (
            f"Expected exit 2 (external sentinel); got {result.returncode}\n"
            f"stderr: {result.stderr}"
        )
        # Assert manifest content in stderr
        assert "External seeds require manual fan-out" in result.stderr, (
            "stderr must contain 'External seeds require manual fan-out'"
        )
        scp_lines = [
            ln for ln in result.stderr.splitlines() if ln.strip().startswith("scp ")
        ]
        assert scp_lines, (
            "stderr must contain at least one line beginning with '  scp '"
        )
        # SC-11: enforce exact `scp <src> <user>@<host>:<target_path>` format
        # (catches drift like missing `user@` or malformed targets).
        import re as _re

        scp_re = _re.compile(r"^scp\s+\S+\s+\w[\w.-]*@\S+:\S+$")
        assert any(scp_re.match(ln.strip()) for ln in scp_lines), (
            f"no scp line matches '<src> <user>@<host>:<path>' format;"
            f" got: {scp_lines!r}"
        )

    def test_full_provision_externals_with_ack_exits_0(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Full provisioning exits 0 when externals present and --ack flag given.

        SC-10: operator passes --ack-external-distribution → no exit-2 sentinel.
        Manifest lines are still printed to stderr (visible for copy-paste).
        RED: flag handling and manifest path don't exist yet.
        Will turn GREEN when T9 wires the exit-2 guard.
        """
        seeds_dir = tmp_path / "nkeys"
        auth_dir = tmp_path / "auth"
        auth_dir.mkdir(parents=True)
        matrix_path = _make_matrix(tmp_path, with_external=True)

        result = _run_genkeys(
            [
                "--regenerate",
                "--yes",
                "--ack-external-distribution",
                "--matrix",
                str(matrix_path),
            ],
            env={
                "SEEDS_DIR": str(seeds_dir),
                "AUTH_DIR": str(auth_dir),
            },
        )

        # Assert exit 0 (no sentinel when operator acknowledged)
        assert result.returncode == 0, (
            f"Expected exit 0 with --ack flag; got {result.returncode}\n"
            f"stderr: {result.stderr}"
        )
        # Manifest must still be visible on stderr even with ack — full format.
        import re as _re

        scp_re = _re.compile(r"^scp\s+\S+\s+\w[\w.-]*@\S+:\S+$")
        scp_lines = [
            ln for ln in result.stderr.splitlines() if ln.strip().startswith("scp ")
        ]
        assert any(scp_re.match(ln.strip()) for ln in scp_lines), (
            f"manifest must still print a full scp '<src> <user>@<host>:<path>'"
            f" line when --ack flag is given; got: {scp_lines!r}"
        )

    def test_external_manifest_uses_sudo_user(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_emit_external_manifest uses SUDO_USER for the scp user, not 'root'.

        SC-11: manifest format is `scp <src> <user>@<host>:<target_path>`.
        `<user>` = SUDO_USER when set; falls back to getpass.getuser() when unset.
        RED: _emit_external_manifest and _operator_user don't exist yet.
        Will turn GREEN when T8 adds those helpers.
        """
        from scripts._acl_models import ExternalDeploy
        from scripts._modes import _emit_external_manifest, _operator_user

        # _operator_user imported for symbol-existence check (T8 introduces it)
        _ = _operator_user

        seeds_dir = tmp_path / "nkeys"
        seeds_dir.mkdir()
        externals: list[tuple[str, ExternalDeploy]] = [
            (
                "voice-client",
                ExternalDeploy(
                    type="external",
                    host="roxabitower",
                    target_path="~/.voicecli/nkeys/voice-client.seed",
                ),
            )
        ]

        # Case 1: SUDO_USER set → manifest uses that username
        monkeypatch.setenv("SUDO_USER", "mickael")
        import io
        import sys as _sys

        captured = io.StringIO()
        orig_stderr = _sys.stderr
        _sys.stderr = captured
        try:
            _emit_external_manifest(externals, seeds_dir)
        finally:
            _sys.stderr = orig_stderr

        manifest = captured.getvalue()
        assert "mickael@roxabitower:" in manifest, (
            f"Manifest must use SUDO_USER 'mickael'; got:\n{manifest}"
        )
        assert "root@" not in manifest, "Manifest must not use 'root' as user"

        # Case 2: SUDO_USER unset → falls back to getpass.getuser()
        monkeypatch.delenv("SUDO_USER", raising=False)
        import getpass

        expected_fallback = getpass.getuser()
        captured2 = io.StringIO()
        _sys.stderr = captured2
        try:
            _emit_external_manifest(externals, seeds_dir)
        finally:
            _sys.stderr = orig_stderr

        manifest2 = captured2.getvalue()
        assert f"{expected_fallback}@roxabitower:" in manifest2, (
            f"Manifest must use getpass.getuser() '{expected_fallback}' when"
            f" SUDO_USER is unset; got:\n{manifest2}"
        )

    def test_regenerate_rollback_not_triggered_on_exit_2(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SystemExit(2) from the external-manifest path must NOT trigger rollback.

        CRITICAL ARCHITECT TEST (spec § Wiring / D-architect-blocker):
        _mode_regenerate wraps _mode_full_provision in try/except BaseException for
        rollback safety. SystemExit IS a BaseException. The exit-2 sentinel must be
        raised by the CALLER (outside the try/except), not inside _mode_full_provision,
        to avoid incorrectly rolling back a successful regen.

        Verification: after _mode_regenerate raises SystemExit(2), the newly-generated
        seed files must still exist in seeds_dir (not replaced by the pre-regen backup).

        RED: exits 1 today (non-root check), not 2; external logic absent.
        Will turn GREEN when T7/T8/T9 wire the external path at the correct call-site.
        """
        import argparse

        from scripts._modes import _mode_regenerate

        # Arrange: a pre-existing seeds_dir with a "backup-era" seed
        seeds_dir = tmp_path / "nkeys"
        auth_dir = tmp_path / "auth"
        auth_dir.mkdir(parents=True)
        seeds_dir.mkdir()
        # Old seed that would be restored by a (wrong) rollback
        old_seed_path = seeds_dir / "hub.seed"
        old_seed_path.write_bytes(b"OLD-SEED-CONTENT")

        matrix_path = _make_matrix(tmp_path, with_external=True)

        monkeypatch.setenv("SEEDS_DIR", str(seeds_dir))
        monkeypatch.setenv("AUTH_DIR", str(auth_dir))
        monkeypatch.setattr(_modes, "_provider_factory", FakeNkeyProvider)

        args = argparse.Namespace(
            matrix=matrix_path,
            yes=True,
            ack_external_distribution=False,
        )

        # Act: _mode_regenerate should raise SystemExit(2) — not roll back seeds
        with pytest.raises(SystemExit) as exc_info:
            _mode_regenerate(args)

        # Assert: exit code is 2 (external sentinel), not 1 (root check) or 0
        assert exc_info.value.code == 2, (
            f"Expected SystemExit(2) from external sentinel path;"
            f" got SystemExit({exc_info.value.code!r})"
        )

        # CRITICAL: newly-generated seeds must survive (no rollback on exit-2)
        # After a successful regen + exit-2, seeds_dir exists with fresh seeds.
        # If rollback fired (wrong), seeds_dir would contain OLD-SEED-CONTENT.
        assert seeds_dir.exists(), "seeds_dir must exist after regen (not rolled back)"
        hub_seed = seeds_dir / "hub.seed"
        assert hub_seed.exists(), "hub.seed must exist after regen"
        # Fake provider writes name.encode() — content would be b"hub"
        assert hub_seed.read_bytes() != b"OLD-SEED-CONTENT", (
            "hub.seed must contain freshly-generated content, not the pre-regen"
            " backup — rollback must NOT fire on SystemExit(2)"
        )

    def test_handle_externals_empty_returns_silently(self, tmp_path: Path) -> None:
        """_handle_externals is a no-op when externals list is empty.

        Covers the `if not externals: return` guard directly. Subprocess tests
        cannot distinguish this branch from "no externals in matrix" — the
        unit test pins the contract.
        """
        import argparse

        from scripts._modes import _handle_externals

        args = argparse.Namespace(ack_external_distribution=False)
        # Must not raise, must not exit
        _handle_externals([], tmp_path, args)

    def test_handle_externals_no_ack_raises_exit_2(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """_handle_externals raises SystemExit(2) when externals present and no ack."""
        import argparse

        from scripts._acl_models import ExternalDeploy
        from scripts._modes import _handle_externals

        externals: list[tuple[str, ExternalDeploy]] = [
            (
                "voice-client",
                ExternalDeploy(
                    type="external",
                    host="roxabitower",
                    target_path="~/.voicecli/nkeys/voice-client.seed",
                ),
            )
        ]
        args = argparse.Namespace(ack_external_distribution=False)
        with pytest.raises(SystemExit) as exc_info:
            _handle_externals(externals, tmp_path, args)
        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "External seeds require manual fan-out" in captured.err

    def test_handle_externals_with_ack_returns_silently(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """_handle_externals does not exit with ack flag; manifest still prints."""
        import argparse

        from scripts._acl_models import ExternalDeploy
        from scripts._modes import _handle_externals

        externals: list[tuple[str, ExternalDeploy]] = [
            (
                "voice-client",
                ExternalDeploy(
                    type="external",
                    host="roxabitower",
                    target_path="~/.voicecli/nkeys/voice-client.seed",
                ),
            )
        ]
        args = argparse.Namespace(ack_external_distribution=True)
        # Must not raise
        _handle_externals(externals, tmp_path, args)
        # But manifest still prints (operator visibility)
        captured = capsys.readouterr()
        assert "External seeds require manual fan-out" in captured.err
