"""RED tests for atomic_write and file permission modes — #1017 T21.

These tests FAIL at collection time because `atomic_write` does not yet
exist in `scripts/gen_nkeys.py`.  That is the intended RED state.

They will turn GREEN when T24 (Slice 2) adds `atomic_write` (A9 from
the spec breadboard) — either inline in `gen_nkeys.py` or via a small
`scripts/_io.py` helper module (open ambiguity in spec).
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

# This import will fail at collection time — that is the intended RED state.
# Once T24 lands, atomic_write is importable from scripts.gen_nkeys (or
# scripts._io if the implementation extracts it there; update the import then).
from scripts.gen_nkeys import atomic_write  # type: ignore[attr-defined]

REPO_ROOT = Path(__file__).resolve().parents[2]
_MATRIX_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "v2-prod.json"


# ── helpers ───────────────────────────────────────────────────────────────────


def _octal_mode(path: Path) -> str:
    """Return the last 4 digits of the octal stat mode string, e.g. '0600'."""
    return oct(os.stat(path).st_mode)[-4:]


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


# ── T21.1 — atomic_write with mode 0600 ──────────────────────────────────────


class TestAtomicWrite:
    def test_atomic_write_user_authconf_mode(self, tmp_path: Path) -> None:
        """atomic_write writes a file with mode 0600 (user auth.conf / seed mode).

        Spec (file modes table): ~/.lyra/nkeys/auth.conf → 0600, operator.
        # verified: removing os.chmod call from atomic_write → mode != 0600 → fails
        """
        # Arrange
        dest = tmp_path / "auth.conf"
        content = "authorization {}\n"

        # Act
        atomic_write(dest, content, 0o600)

        # Assert
        assert dest.exists(), "atomic_write must create the destination file"
        assert _octal_mode(dest) == "0600", (
            f"Expected mode 0600; got {_octal_mode(dest)}"
        )
        assert dest.read_text() == content

    def test_atomic_write_system_authconf_mode(self, tmp_path: Path) -> None:
        """atomic_write writes a file with mode 0640 (system auth.conf mode).

        Spec (file modes table): /etc/nats/nkeys/auth.conf → 0640, root:nats.
        # verified: removing os.chmod call from atomic_write → mode != 0640 → fails
        """
        # Arrange
        dest = tmp_path / "auth.conf"
        content = "# system auth.conf\nauthorization {}\n"

        # Act
        atomic_write(dest, content, 0o640)

        # Assert
        assert _octal_mode(dest) == "0640", (
            f"Expected mode 0640; got {_octal_mode(dest)}"
        )

    def test_atomic_write_seed_mode(self, tmp_path: Path) -> None:
        """atomic_write writes a file with mode 0600 for seed files.

        Spec (file modes table): ~/.lyra/nkeys/<name>.seed → 0600, operator.
        # verified: removing os.chmod from atomic_write → seed file readable → fails
        """
        # Arrange
        dest = tmp_path / "hub.seed"
        content = "SUAABCDEFGHIJKLMNOPQRSTUVWXYZ234567EXAMPLEONLY\n"

        # Act
        atomic_write(dest, content, 0o600)

        # Assert
        assert _octal_mode(dest) == "0600", (
            f"Expected mode 0600 for seed file; got {_octal_mode(dest)}"
        )

    def test_atomic_write_is_atomic(self, tmp_path: Path) -> None:
        """atomic_write leaves no temp file after a successful write.

        Spec (Atomicity): NamedTemporaryFile → os.chmod → os.replace.
        After the call, only the destination file must exist; no .tmp* siblings.
        # verified: removing os.replace (leaving only tmp) → glob finds tmp → fails
        """
        # Arrange
        dest = tmp_path / "auth.conf"
        content = "authorization {}\n"

        # Act
        atomic_write(dest, content, 0o600)

        # Assert — no temp files left in the directory
        siblings = list(tmp_path.iterdir())
        found = [p.name for p in siblings]
        assert siblings == [dest], (
            f"Expected only {dest.name} in tmp_path; found: {found}"
        )
        assert dest.read_text() == content

    def test_atomic_write_uses_os_replace_in_same_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """atomic_write calls os.replace exactly once with (tmp_in_same_dir, dest).

        Guards the SIGHUP-safe contract: the temp file must be created in the
        same directory as the target so the final os.replace is an intra-filesystem
        rename (atomic from the OS perspective).  A cross-device copy would NOT be
        atomic and could expose a partial file to NATS on SIGHUP reload.

        # verified: replacing os.replace with shutil.move → recorded call uses
        # different function → this test catches the regression.
        # verified: patching mkstemp to return a path in /tmp (different dir) →
        # parent-equality assertion fails → test catches the regression.
        """
        import scripts._modes as _modes

        dest = tmp_path / "auth.conf"
        recorded: list[tuple[str, str]] = []

        real_replace = os.replace

        def capturing_replace(src: str, dst: str) -> None:
            recorded.append((src, dst))
            real_replace(src, dst)

        monkeypatch.setattr(_modes.os, "replace", capturing_replace)

        atomic_write(dest, "CONTENT", 0o600)

        # Exactly one rename
        assert len(recorded) == 1, (
            f"os.replace must be called once; got {len(recorded)}: {recorded}"
        )
        src_path, dst_path = Path(recorded[0][0]), Path(recorded[0][1])

        # Temp file must live in the same directory as the target (same filesystem)
        assert src_path.parent == dest.parent, (
            f"temp parent {src_path.parent!r} != target {dest.parent!r}; "
            "cross-dir rename is not atomic (partial auth.conf on SIGHUP)"
        )
        # Destination must be exactly the requested path
        assert dst_path == dest, (
            f"os.replace destination {dst_path!r} != requested target {dest!r}"
        )
        # File must actually land (real_replace was called)
        assert dest.exists() and dest.read_text() == "CONTENT"

    def test_atomic_write_final_content_and_mode(self, tmp_path: Path) -> None:
        """Real atomic_write: content correct, mode 0600, no temp-file litter.

        Validates end-to-end: the destination file has the exact content written,
        mode bits are 0600, and no leftover temp files remain in the directory.

        # verified: removing os.chmod → stat.S_IMODE != 0o600 → fails
        # verified: returning before os.replace → dest absent → exists() fails
        """
        dest = tmp_path / "auth.conf"

        atomic_write(dest, "CONTENT", 0o600)

        assert dest.exists(), "destination must exist after atomic_write"
        assert dest.read_text() == "CONTENT", "file content must match what was written"
        assert stat.S_IMODE(os.stat(dest).st_mode) == 0o600, (
            f"Expected mode 0o600; got {oct(stat.S_IMODE(os.stat(dest).st_mode))}"
        )
        # No temp-file litter — only the final destination must remain
        remaining = list(tmp_path.iterdir())
        assert remaining == [dest], (
            f"Expected only auth.conf; found: {[p.name for p in remaining]}"
        )

    def test_atomic_write_exception_cleanup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """atomic_write cleans up the temp file when os.replace raises.

        Guards the exception path: if the rename fails (e.g. cross-device error,
        permission denied), atomic_write must unlink the temp file and re-raise.
        No temp litter must survive the failure.

        # verified: removing os.unlink(tmp) from the except block → temp file
        # remains in tmp_path after the exception → iterdir() finds it → fails
        """
        import scripts._modes as _modes

        dest = tmp_path / "auth.conf"

        def failing_replace(_src: str, _dst: str) -> None:
            raise OSError("simulated rename failure")

        monkeypatch.setattr(_modes.os, "replace", failing_replace)

        with pytest.raises(OSError, match="simulated rename failure"):
            atomic_write(dest, "CONTENT", 0o600)

        # No temp-file litter must remain after the exception
        remaining = list(tmp_path.iterdir())
        assert remaining == [], (
            f"dir not empty after failed write: {[p.name for p in remaining]}"
        )


# ── T21.5 — mode after --regen-authconf ──────────────────────────────────────


class TestUserAuthconfModeAfterRegen:
    def test_user_authconf_mode_after_regen(self, tmp_path: Path) -> None:
        """After --regen-authconf the written auth.conf has mode 0600.

        SC-2 + file modes table: --regen-authconf writes to SEEDS_DIR/auth.conf
        with mode 0600 (user-owned, not readable by group/other).
        Will FAIL now: --regen-authconf raises
        SystemExit("not yet implemented in this slice").
        # verified: removing os.chmod(0o600) in regen_authconf handler
        # → mode != 0600 → fails
        """
        # Arrange — write fake seeds for all active identities
        seeds_dir = tmp_path / "nkeys"
        matrix_data = json.loads(_MATRIX_FIXTURE.read_text())
        active_identities = [
            name
            for name, ident in matrix_data["identities"].items()
            if ident["status"] == "active"
        ]
        seeds_dir.mkdir(parents=True, exist_ok=True)
        for name in active_identities:
            seed_file = seeds_dir / f"{name}.seed"
            seed_file.write_bytes(name.encode())
            seed_file.chmod(0o600)

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
        auth_conf = seeds_dir / "auth.conf"
        assert auth_conf.exists(), "auth.conf must exist after --regen-authconf"
        assert _octal_mode(auth_conf) == "0600", (
            f"Expected mode 0600 for user auth.conf; got {_octal_mode(auth_conf)}"
        )
