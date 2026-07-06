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
        factory_identities = [
            name
            for name, ident in matrix_data["identities"].items()
            if ident["status"] == "active" and ident["owner"] == "factory"
        ]
        voicecli_identities = [
            name
            for name, ident in matrix_data["identities"].items()
            if ident["status"] == "active" and ident["owner"] == "voicecli"
        ]
        _write_fake_seeds(seeds_dir, factory_identities)
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

    def test_regenerate_with_yes_rootless_succeeds(self, tmp_path: Path) -> None:
        """--regenerate --yes without root must exit 0 (rootless by default).

        NEW CONTRACT (#1718 / #7): --regenerate is rootless by default.
        Root is only required for the opt-in /etc/nats write path
        (FACTORY_ACL_WRITE_ETC_NATS=1). Without that env var, --regenerate --yes
        must write seeds to factory_data_dir()/nkeys and exit 0.

        Verified: reverting _mode_full_provision to always call _require_root()
        would cause a non-zero exit here (root check fires → exit 1).
        """
        # Arrange — tmp dirs so no disk pollution; AUTH_DIR set to prevent any
        # accidental /etc path access
        seeds_dir = tmp_path / "nkeys"
        auth_dir = tmp_path / "auth"
        auth_dir.mkdir(parents=True)

        # Act — rootless, --yes, FACTORY_ACL_WRITE_ETC_NATS NOT set
        result = _run_genkeys(
            [
                "--regenerate",
                "--yes",
                "--matrix",
                str(_MATRIX_FIXTURE),
            ],
            env={
                "SEEDS_DIR": str(seeds_dir),
                "AUTH_DIR": str(auth_dir),
                "ROTATION_LOG": str(tmp_path / "rotation-log.md"),
                "OPERATOR_LOG": str(tmp_path / "operator.log"),
            },
        )

        # Assert — rootless path exits 0
        assert result.returncode == 0, (
            "Expected exit 0 for rootless --regenerate --yes; "
            f"got {result.returncode}\nstderr: {result.stderr}"
        )
        assert "not yet implemented" not in result.stderr
        # Seeds must have been written to seeds_dir
        assert seeds_dir.exists(), "seeds_dir must be created by --regenerate"
        user_conf = seeds_dir / "auth.conf"
        assert user_conf.exists(), (
            "auth.conf must be written to seeds_dir by rootless --regenerate"
        )

    def test_regenerate_with_etc_nats_opt_in_requires_root(
        self, tmp_path: Path
    ) -> None:
        """--regenerate with FACTORY_ACL_WRITE_ETC_NATS=1 requires root (or AUTH_DIR).

        When the opt-in env var is set, _require_root() is called. Without real root
        AND without an AUTH_DIR override, the process must exit non-zero with an
        appropriate error.

        Verified: removing the `if write_etc: _require_root()` guard from
        _mode_full_provision would cause exit 0 here instead of non-zero.
        """
        # Arrange — run without AUTH_DIR override so _require_root() fires for real
        seeds_dir = tmp_path / "nkeys"
        seeds_dir.mkdir(parents=True)

        result = _run_genkeys(
            [
                "--regenerate",
                "--yes",
                "--matrix",
                str(_MATRIX_FIXTURE),
            ],
            env={
                "SEEDS_DIR": str(seeds_dir),
                # AUTH_DIR deliberately NOT set — _require_root() checks real uid
                "FACTORY_ACL_WRITE_ETC_NATS": "1",
                "ROTATION_LOG": str(tmp_path / "rotation-log.md"),
                "OPERATOR_LOG": str(tmp_path / "operator.log"),
            },
        )

        # Assert — non-root CI must exit non-zero (root check fires)
        assert result.returncode != 0, (
            "Expected non-zero exit for --regenerate --yes with "
            "FACTORY_ACL_WRITE_ETC_NATS=1 on a non-root user; "
            f"got {result.returncode}\nstderr: {result.stderr}"
        )
        assert "not yet implemented" not in result.stderr

    def test_regenerate_restores_on_provision_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Covers the "backup exists, seeds_dir absent" branch of _restore_on_failure.

        _mode_full_provision is mocked away entirely here, so seeds_dir is
        never recreated after _backup_seeds wipes it — this test alone would
        pass even against the old dead guard, since seeds_dir.exists() is
        False either way. See test_regenerate_restores_on_genuine_mid_loop_failure
        below for the complementary "backup exists, seeds_dir re-populated"
        branch, which this full mock cannot exercise.
        """
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
        monkeypatch.setenv("ROTATION_LOG", str(tmp_path / "rotation-log.md"))
        monkeypatch.setenv("OPERATOR_LOG", str(tmp_path / "operator.log"))

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

    def test_regenerate_restores_on_genuine_mid_loop_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """seeds_dir is restored even when the failure is genuinely mid-loop.

        Regression for #2258: the test above (`test_regenerate_restores_on_
        provision_failure`) mocks `_mode_full_provision` away entirely, so it
        never touches the filesystem and seeds_dir stays absent — that's why
        it passed even with the original buggy guard
        (`not seeds_dir.exists()`). A REAL mid-loop failure is different:
        `_mode_full_provision` recreates seeds_dir and writes seeds
        identity-by-identity, so by the time a later identity fails,
        seeds_dir already exists again (partially populated) — the old guard
        skipped restoration in exactly this case.

        This test drives a provider that succeeds for the first active
        identity and raises on the second, so seeds_dir genuinely contains
        one freshly-written (partial) seed file at the moment
        `_restore_on_failure` runs.
        """
        import argparse

        from scripts._modes import _mode_regenerate

        class _FailsOnSecondCall:
            def __init__(self) -> None:
                self._calls = 0

            def ensure_available(self) -> None:
                return None

            def gen_seed(self, name: str) -> bytes:
                self._calls += 1
                if self._calls >= 2:
                    raise RuntimeError("simulated nk failure mid-loop")
                return name.encode()

            def pubkey_from_seed(self, seed: bytes) -> str:
                return FakeNkeyProvider().pubkey_from_seed(seed)

        # Arrange: seeds_dir with pre-existing (backup-era) seed + auth.conf.
        seeds_dir = tmp_path / "nkeys"
        auth_dir = tmp_path / "auth"
        auth_dir.mkdir()
        seeds_dir.mkdir()
        (seeds_dir / "hub.seed").write_bytes(b"original-hub-seed")
        (seeds_dir / "hub.seed").chmod(0o600)
        (seeds_dir / "auth.conf").write_text("original-auth-conf")

        monkeypatch.setenv("SEEDS_DIR", str(seeds_dir))
        monkeypatch.setenv("AUTH_DIR", str(auth_dir))
        # Hermetic by construction, not by accident of where the induced
        # failure lands in _mode_full_provision's loop (#2246 review) — see
        # test_regenerate_mid_loop_failure_writes_no_rotation_log_entries
        # below, which pins the same env vars for the same reason.
        monkeypatch.setenv("ROTATION_LOG", str(tmp_path / "rotation-log.md"))
        monkeypatch.setenv("OPERATOR_LOG", str(tmp_path / "operator.log"))
        monkeypatch.setattr(_modes, "_provider_factory", _FailsOnSecondCall)

        args = argparse.Namespace(yes=True, matrix=_MATRIX_FIXTURE)

        with pytest.raises(RuntimeError, match="simulated nk failure mid-loop"):
            _mode_regenerate(args)

        # Assert: seeds_dir must match the pre-regen backup exactly — not the
        # partial write (first identity's freshly-generated seed) left behind
        # by the failed mid-loop attempt.
        assert seeds_dir.exists(), (
            "seeds_dir must be restored from backup after a genuine mid-loop failure"
        )
        assert (seeds_dir / "hub.seed").read_bytes() == b"original-hub-seed", (
            "hub.seed must match the pre-regen backup, not the partial write"
            " from the failed mid-loop attempt"
        )
        assert (seeds_dir / "auth.conf").read_text() == "original-auth-conf", (
            "auth.conf must match the pre-regen backup after a mid-loop failure"
        )
        assert (seeds_dir / "hub.seed").stat().st_mode & 0o777 == 0o600, (
            "restored seed file must retain 0600 permissions (credential material)"
        )

    def test_regenerate_restores_etc_auth_on_genuine_mid_loop_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """auth_dir/auth.conf is restored even when a new one was already written.

        Regression for #2258 review follow-up: `_restore_on_failure`'s
        write_etc branch (scripts/_modes.py:436-441) mirrors the same dead-guard
        fix as the seeds_dir branch, but had zero coverage. Simply enabling
        FACTORY_ACL_WRITE_ETC_NATS on the seeds-focused mid-loop test above
        would NOT falsify it: that test's failure fires during the seed-gen
        loop (_mode_full_provision:587-593), which runs BEFORE the system
        auth.conf is (re)written at line 603 — so at restore time
        auth_conf.exists() is False regardless of the guard, and the old dead
        guard (`not auth_conf.exists()`) would restore anyway by accident.

        This test lets the seed-gen loop AND the system auth.conf write
        (line 603) both succeed, then fails on the second `atomic_write` call
        (the seeds_dir auth.conf write, line 611) — so a freshly-written
        system auth.conf genuinely exists at restore time. That is the only
        shape that exercises the guard: the old code would see
        auth_conf.exists() and skip restoring, leaving the new (mid-failure)
        auth.conf in place instead of the pre-regen backup.
        """
        import argparse

        from scripts._modes import _mode_regenerate

        seeds_dir = tmp_path / "nkeys"
        auth_dir = tmp_path / "auth"
        auth_dir.mkdir()
        seeds_dir.mkdir()
        (seeds_dir / "hub.seed").write_bytes(b"original-hub-seed")
        (seeds_dir / "auth.conf").write_text("original-auth-conf")
        (auth_dir / "auth.conf").write_text("original-etc-auth-conf")

        monkeypatch.setenv("SEEDS_DIR", str(seeds_dir))
        monkeypatch.setenv("AUTH_DIR", str(auth_dir))
        # Hermetic by construction, not by accident of where the induced
        # failure lands in _mode_full_provision's loop (#2246 review) — see
        # test_regenerate_mid_loop_failure_writes_no_rotation_log_entries
        # below, which pins the same env vars for the same reason.
        monkeypatch.setenv("ROTATION_LOG", str(tmp_path / "rotation-log.md"))
        monkeypatch.setenv("OPERATOR_LOG", str(tmp_path / "operator.log"))
        monkeypatch.setenv("FACTORY_ACL_WRITE_ETC_NATS", "1")
        monkeypatch.setattr(_modes, "_provider_factory", FakeNkeyProvider)

        # Let every seed-file write (line 591, one per identity) and the
        # system auth.conf write (line 603, auth_dir/auth.conf) succeed for
        # real; fail only on the LAST write (line 611, seeds_dir/auth.conf) —
        # this is order-independent (doesn't matter how many active
        # identities the matrix fixture has) and guarantees the failure lands
        # strictly after line 603.
        real_atomic_write = _modes.atomic_write
        user_conf_path = seeds_dir / "auth.conf"

        def _fails_on_user_conf_write(path: Path, content: str, mode: int) -> None:
            if path == user_conf_path:
                raise RuntimeError("simulated failure after system auth.conf write")
            real_atomic_write(path, content, mode)

        monkeypatch.setattr(_modes, "atomic_write", _fails_on_user_conf_write)

        args = argparse.Namespace(yes=True, matrix=_MATRIX_FIXTURE)

        with pytest.raises(
            RuntimeError, match="simulated failure after system auth.conf write"
        ):
            _mode_regenerate(args)

        # The system auth.conf (line 603) was written before the induced
        # failure — confirms the dead-guard scenario is genuinely exercised,
        # not accidentally absent like the seeds-focused mid-loop test above.
        assert (auth_dir / "auth.conf").exists()
        assert (auth_dir / "auth.conf").read_text() == "original-etc-auth-conf", (
            "auth_dir/auth.conf must be restored to the pre-regen backup, not"
            " the freshly-written content produced before the induced failure"
        )

    def test_regenerate_mid_loop_failure_writes_no_rotation_log_entries(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A REAL (non-mocked) failure partway through _mode_full_provision's
        per-identity loop must leave rotation-log.md with ZERO entries.

        Regression guard (#2246 review, backend-dev finding): before the
        buffer-then-flush fix, rotation_log_append() fired inline inside the
        loop, so an identity processed before the failing one (here: "hub",
        processed before "clipool-worker" raises) would already have a
        "freshly rotated" line in rotation-log.md by the time
        _restore_on_failure() reverts seeds_dir back to its pre-rotation
        backup — desyncing the log from the (reverted) real seed state and
        suppressing check_seed_age.py's WARN/FAIL for that identity for up to
        ~75-90 days. Unlike test_regenerate_restores_on_provision_failure
        (which fully mocks out _mode_full_provision via an immediate
        side_effect and therefore never reaches the per-identity loop), this
        test lets the loop run for real and fails partway through it.
        """
        import argparse

        from scripts._modes import _mode_regenerate

        seeds_dir = tmp_path / "nkeys"
        auth_dir = tmp_path / "auth"
        auth_dir.mkdir()
        seeds_dir.mkdir()
        (seeds_dir / "hub.seed").write_bytes(b"original-hub-seed")
        (seeds_dir / "clipool-worker.seed").write_bytes(b"original-clipool-seed")
        rotation_log = tmp_path / "rotation-log.md"

        monkeypatch.setenv("SEEDS_DIR", str(seeds_dir))
        monkeypatch.setenv("AUTH_DIR", str(auth_dir))
        monkeypatch.setenv("ROTATION_LOG", str(rotation_log))
        monkeypatch.setenv("OPERATOR_LOG", str(tmp_path / "operator.log"))

        class _FlakyProvider(FakeNkeyProvider):
            """Succeeds on 'hub' (processed first), raises on 'clipool-worker'
            (processed second) — a real mid-loop failure, not a mock."""

            def gen_seed(self, name: str) -> bytes:
                if name == "clipool-worker":
                    raise RuntimeError(f"simulated seed-gen failure for {name}")
                return super().gen_seed(name)

        monkeypatch.setattr(_modes, "_provider_factory", _FlakyProvider)

        matrix_path = _make_matrix(tmp_path, with_external=False)
        args = argparse.Namespace(
            yes=True, matrix=matrix_path, ack_external_distribution=False
        )

        with pytest.raises(RuntimeError, match="simulated seed-gen failure"):
            _mode_regenerate(args)

        # rotation-log.md must have ZERO entries — not one for "hub" (processed
        # successfully before the failure) with none for "clipool-worker".
        # Before the buffer-then-flush fix, the inline rotation_log_append()
        # call would have already written a "secret:hub" line by this point.
        #
        # NOTE: this test deliberately does NOT assert on-disk seed content
        # (i.e. that seeds_dir was restored from its pre-rotation backup).
        # _restore_on_failure()'s dead-guard bug (#2258) — where a genuine
        # mid-loop failure left seeds_dir un-restored because the guard read
        # "already exists" as "already restored" — is fixed above (see
        # test_regenerate_restores_on_genuine_mid_loop_failure) and exercised
        # there, not here. This test's contract is narrower and independent
        # of it: regardless of seed-restore correctness, the rotation log
        # must never claim an identity was freshly rotated when its seed
        # generation didn't durably complete for the whole batch.
        content = rotation_log.read_text() if rotation_log.exists() else ""
        secret_lines = [ln for ln in content.splitlines() if "secret:" in ln]
        assert secret_lines == [], (
            "rotation-log.md must have zero 'secret:' lines after a mid-loop"
            f" failure; got {secret_lines!r}"
        )

    def test_full_provision_log_failure_does_not_trigger_seed_rollback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A rotation-log FLUSH failure must not roll back already-good seeds.

        Pins the other direction of the buffer-then-flush invariant: the
        flush loop runs after every seed + auth.conf write has already
        succeeded, so a failure in the logging bridge itself (_run_bash) must
        fail soft (per src/factory/operator_audit.py's hardening — a
        subprocess timeout/OSError is caught, never raised) rather than
        propagating into _mode_regenerate's rollback path and reverting seeds
        that were never actually compromised.
        """
        import argparse
        import subprocess

        from scripts._modes import _mode_regenerate

        from factory import operator_audit

        seeds_dir = tmp_path / "nkeys"
        auth_dir = tmp_path / "auth"
        auth_dir.mkdir()
        seeds_dir.mkdir()
        (seeds_dir / "hub.seed").write_bytes(b"original-hub-seed")
        (seeds_dir / "clipool-worker.seed").write_bytes(b"original-clipool-seed")

        monkeypatch.setenv("SEEDS_DIR", str(seeds_dir))
        monkeypatch.setenv("AUTH_DIR", str(auth_dir))
        monkeypatch.setenv("ROTATION_LOG", str(tmp_path / "rotation-log.md"))
        monkeypatch.setenv("OPERATOR_LOG", str(tmp_path / "operator.log"))
        monkeypatch.setattr(_modes, "_provider_factory", FakeNkeyProvider)

        def _raise_timeout(*_args: object, **_kwargs: object) -> None:
            raise subprocess.TimeoutExpired(cmd="bash", timeout=1)

        monkeypatch.setattr(operator_audit.subprocess, "run", _raise_timeout)

        matrix_path = _make_matrix(tmp_path, with_external=False)
        args = argparse.Namespace(
            yes=True, matrix=matrix_path, ack_external_distribution=False
        )

        # Act — must NOT raise despite every rotation-log flush call failing.
        _mode_regenerate(args)

        # Assert — new seeds are in place; the logging failure did not trigger
        # a rollback to the pre-rotation backup.
        assert (seeds_dir / "hub.seed").read_bytes() != b"original-hub-seed", (
            "a rotation-log flush failure must not roll back freshly generated seeds"
        )


# ── T19.5 — --show rootless reads seeds_dir + opt-in requires root ───────────


class TestShowMode:
    def test_show_rootless_reads_seeds_dir(self, tmp_path: Path) -> None:
        """--show (rootless default) reads SEEDS_DIR/auth.conf and exits 0.

        NEW CONTRACT (#1718 / #7): --show is rootless by default.
        It reads factory_data_dir()/nkeys/auth.conf (via _seeds_dir()).
        Root is only required when FACTORY_ACL_WRITE_ETC_NATS=1 is set.

        Verified: reverting _mode_show to always call _require_root() would
        cause a non-zero exit here on non-root CI.
        """
        # Arrange — write an auth.conf into the tmp seeds dir
        seeds_dir = tmp_path / "nkeys"
        seeds_dir.mkdir(parents=True)
        auth_conf = seeds_dir / "auth.conf"
        auth_conf.write_text("authorization { # test content }\n")
        auth_conf.chmod(0o600)

        # Act
        result = _run_genkeys(
            [
                "--show",
                "--matrix",
                str(_MATRIX_FIXTURE),
            ],
            env={"SEEDS_DIR": str(seeds_dir)},
        )

        # Assert — exits 0 and prints the auth.conf content
        assert result.returncode == 0, (
            f"Expected exit 0 for rootless --show; got {result.returncode}\n"
            f"stderr: {result.stderr}"
        )
        assert "authorization" in result.stdout, (
            f"--show must print auth.conf content to stdout; got: {result.stdout!r}"
        )
        assert "not yet implemented" not in result.stderr

    def test_show_opt_in_requires_root(self, tmp_path: Path) -> None:
        """--show with FACTORY_ACL_WRITE_ETC_NATS=1 requires root (exits non-zero).

        When the opt-in env var is set, _mode_show calls _require_root().
        Without real root AND without an AUTH_DIR override, the process must
        exit non-zero.

        Verified: removing the `if _etc_nats_write_enabled(): _require_root()`
        guard from _mode_show would cause exit 0 here instead of non-zero.
        """
        # Arrange — seeds_dir exists (to avoid the missing-auth.conf exit-1 path)
        seeds_dir = tmp_path / "nkeys"
        seeds_dir.mkdir(parents=True)

        # Act — AUTH_DIR deliberately NOT set so _require_root() checks real uid
        result = _run_genkeys(
            [
                "--show",
                "--matrix",
                str(_MATRIX_FIXTURE),
            ],
            env={
                "SEEDS_DIR": str(seeds_dir),
                "FACTORY_ACL_WRITE_ETC_NATS": "1",
            },
        )

        # Assert — non-root CI must exit non-zero (root check fires)
        assert result.returncode != 0, (
            "Expected non-zero exit for --show with FACTORY_ACL_WRITE_ETC_NATS=1 "
            f"on a non-root user; got {result.returncode}\nstderr: {result.stderr}"
        )
        assert "not yet implemented" not in result.stderr


# ── T19.6 — default mode rootless write + opt-in dual-write ──────────────────


class TestDefaultModeWrite:
    def test_default_mode_writes_seeds_dir_only(self, tmp_path: Path) -> None:
        """Default mode (no flag, no opt-in) writes auth.conf to seeds_dir ONLY.

        NEW CONTRACT (#1718 / #7): default full-provision is rootless and writes
        exclusively to factory_data_dir()/nkeys/auth.conf. It does NOT write to
        AUTH_DIR (/etc/nats/nkeys) unless FACTORY_ACL_WRITE_ETC_NATS=1 is set.

        Verified: reverting _mode_full_provision to always write to _auth_dir()
        would cause the AUTH_DIR file to appear, breaking the "must not exist"
        assertion below.
        """
        # Arrange — separate dirs so we can assert AUTH_DIR is untouched
        system_auth_dir = tmp_path / "etc_nats_nkeys"
        system_auth_dir.mkdir(parents=True)
        user_seeds_dir = tmp_path / "user_nkeys"
        user_seeds_dir.mkdir(parents=True)

        # Act — no FACTORY_ACL_WRITE_ETC_NATS set
        result = _run_genkeys(
            [
                "--matrix",
                str(_MATRIX_FIXTURE),
            ],
            env={
                "SEEDS_DIR": str(user_seeds_dir),
                "AUTH_DIR": str(system_auth_dir),
                "ROTATION_LOG": str(tmp_path / "rotation-log.md"),
                "OPERATOR_LOG": str(tmp_path / "operator.log"),
            },
        )

        system_conf = system_auth_dir / "auth.conf"
        user_conf = user_seeds_dir / "auth.conf"

        assert result.returncode == 0, (
            f"Expected exit 0 for rootless default mode; got {result.returncode}\n"
            f"stderr: {result.stderr}"
        )
        # Primary write: seeds_dir/auth.conf must exist
        assert user_conf.exists(), (
            "auth.conf must be written to SEEDS_DIR by rootless default mode"
        )
        # System path: AUTH_DIR must NOT be written in default (rootless) mode
        assert not system_conf.exists(), (
            "auth.conf must NOT be written to AUTH_DIR in default (rootless) mode; "
            "set FACTORY_ACL_WRITE_ETC_NATS=1 to opt-in to the /etc/nats dual-write"
        )
        assert "not yet implemented" not in result.stderr

    def test_opt_in_mode_writes_both_seeds_dir_and_auth_dir(
        self, tmp_path: Path
    ) -> None:
        """With FACTORY_ACL_WRITE_ETC_NATS=1 default mode writes BOTH targets.

        The opt-in /etc/nats dual-write is vestigial (host nats.service retired)
        but must remain functional. AUTH_DIR override bypasses the real root check
        so this test runs without sudo.

        Verified: removing the `if write_etc:` block from _mode_full_provision
        would cause system_conf to remain absent, failing the second assertion.
        """
        # Arrange
        system_auth_dir = tmp_path / "etc_nats_nkeys"
        system_auth_dir.mkdir(parents=True)
        user_seeds_dir = tmp_path / "user_nkeys"
        user_seeds_dir.mkdir(parents=True)

        # Act — opt-in enabled; AUTH_DIR override bypasses _require_root()
        result = _run_genkeys(
            [
                "--matrix",
                str(_MATRIX_FIXTURE),
            ],
            env={
                "SEEDS_DIR": str(user_seeds_dir),
                "AUTH_DIR": str(system_auth_dir),
                "FACTORY_ACL_WRITE_ETC_NATS": "1",
                "ROTATION_LOG": str(tmp_path / "rotation-log.md"),
                "OPERATOR_LOG": str(tmp_path / "operator.log"),
            },
        )

        system_conf = system_auth_dir / "auth.conf"
        user_conf = user_seeds_dir / "auth.conf"

        assert result.returncode == 0, (
            f"Expected exit 0 for opt-in dual-write mode; got {result.returncode}\n"
            f"stderr: {result.stderr}"
        )
        # Both targets must be written when opt-in is active
        assert system_conf.exists(), (
            "System auth.conf must be written to AUTH_DIR when opt-in flag set"
        )
        assert user_conf.exists(), (
            "User auth.conf must be written to SEEDS_DIR even in opt-in mode"
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
            "publish": ["factory.out.>"],
            "subscribe": ["factory.in.>"],
            "deploy": {"type": "container", "secret": "lyra-nats-hub"},
        },
        "clipool-worker": {
            "status": "active",
            "created_at": "2026-01-01",
            "owner": "lyra",
            "description": "clipool worker",
            "allow_responses": True,
            "publish": ["factory.clipool.heartbeat"],
            "subscribe": ["factory.jobs.claude"],
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
            "publish": ["factory.voice.>"],
            "subscribe": ["factory.voice.response.>"],
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
        monkeypatch.setenv("ROTATION_LOG", str(tmp_path / "rotation-log.md"))
        monkeypatch.setenv("OPERATOR_LOG", str(tmp_path / "operator.log"))
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
                "ROTATION_LOG": str(tmp_path / "rotation-log.md"),
                "OPERATOR_LOG": str(tmp_path / "operator.log"),
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
                "ROTATION_LOG": str(tmp_path / "rotation-log.md"),
                "OPERATOR_LOG": str(tmp_path / "operator.log"),
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
        monkeypatch.setenv("ROTATION_LOG", str(tmp_path / "rotation-log.md"))
        monkeypatch.setenv("OPERATOR_LOG", str(tmp_path / "operator.log"))
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


# ── N2/N10 — rotation-log wiring (#2246) ────────────────────────────────────


class TestRotationLogWiring:
    """#2246: _mode_full_provision calls rotation_log_append() per active identity."""

    def test_full_provision_writes_one_rotation_log_line_per_active_identity(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """rotation-log.md gains exactly K lines for K active identities.

        Spec trace: SC2 (N2, N10).
        """
        import argparse

        from scripts._modes import _mode_full_provision

        seeds_dir = tmp_path / "nkeys"
        auth_dir = tmp_path / "auth"
        auth_dir.mkdir(parents=True)
        rotation_log = tmp_path / "rotation-log.md"
        monkeypatch.setenv("SEEDS_DIR", str(seeds_dir))
        monkeypatch.setenv("AUTH_DIR", str(auth_dir))
        monkeypatch.setenv("ROTATION_LOG", str(rotation_log))
        monkeypatch.setenv("OPERATOR_LOG", str(tmp_path / "operator.log"))
        monkeypatch.setattr(_modes, "_provider_factory", FakeNkeyProvider)

        # K=2 active identities (hub, clipool-worker) — no externals needed.
        matrix_path = _make_matrix(tmp_path, with_external=False)
        active_names = {"hub", "clipool-worker"}
        args = argparse.Namespace(
            matrix=matrix_path, yes=True, ack_external_distribution=False
        )

        # Act
        _mode_full_provision(args)

        # Assert — exactly one secret:<name> line per active identity, with the
        # reason/trigger pinned to the full-provision call site (not just a
        # substring match on the name — a swapped trigger string would stay
        # green against the name-only assertion).
        content = rotation_log.read_text() if rotation_log.exists() else ""
        for name in active_names:
            matching = [ln for ln in content.splitlines() if f"secret:{name}" in ln]
            assert len(matching) == 1, (
                f"expected exactly one 'secret:{name}' line in rotation-log.md;"
                f" got {matching!r} (rotation_log.exists()={rotation_log.exists()})"
            )
            line = matching[0]
            assert "reason:seed-generated" in line, (
                f"expected 'reason:seed-generated' in line: {line!r}"
            )
            assert "trigger:factory-acl-genkeys" in line, (
                f"expected 'trigger:factory-acl-genkeys' in line: {line!r}"
            )
