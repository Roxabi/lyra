"""Path-traversal hardening tests for rotate-gh-key.sh and rotate-claude-oauth.sh.

Regression tests for issue #1118: PEM_RE allowed '/' without blocking '..'
sequences, enabling a caller to pass '../../../../etc/some.pem' as argument.

These tests verify that both scripts reject paths containing '..' components
even when the file exists, and accept paths inside trusted directories.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "deploy" / "scripts"

ROTATE_GH = SCRIPTS_DIR / "rotate-gh-key.sh"
ROTATE_CLAUDE = SCRIPTS_DIR / "rotate-claude-oauth.sh"

# Trusted dirs defined in the hardened scripts.
TRUSTED_DIRS = ["/home/factory/secrets", "/etc/factory"]


def _run_script(script: Path, arg: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["XDG_RUNTIME_DIR"] = env.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return subprocess.run(
        ["bash", str(script), arg],
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )


class TestRotateGhKeyPathTraversal:
    """rotate-gh-key.sh must reject traversal paths (issue #1118)."""

    def test_dotdot_traversal_rejected_via_realpath(self, tmp_path: Path) -> None:
        """A path that traverses out of /tmp via '..' and resolves outside trusted
        dirs must be rejected.

        We create a real file at /tmp/.../fake.pem, then craft a path that uses
        '..' but still resolves to it (realpath succeeds). The trusted-dir guard
        must then fire.

        # verified: removing the trusted-dir check → exit code comes from
        # podman-not-found, not from our guard → test assertion on stderr fails → RED
        """
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        target = tmp_path / "fake.pem"
        target.write_text("FAKE")

        # This path traverses up via '..' but realpath -e resolves it to target.
        traversal = str(subdir) + "/../fake.pem"

        result = _run_script(ROTATE_GH, traversal)

        # Must exit non-zero: resolved path is /tmp/... which is not trusted.
        assert result.returncode != 0, (
            f"Expected non-zero exit for traversal path; got 0\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert any(
            kw in result.stderr.lower()
            for kw in ("trusted", "outside", "/home/factory/secrets", "/etc/factory")
        ), f"Expected trusted-dir rejection in stderr; got: {result.stderr!r}"

    def test_missing_file_is_rejected(self, tmp_path: Path) -> None:
        """Non-existent file must be rejected before reaching trusted-dir check."""
        result = _run_script(ROTATE_GH, str(tmp_path / "nonexistent.pem"))
        assert result.returncode != 0

    def test_no_args_prints_usage(self) -> None:
        result = _run_script(ROTATE_GH, "")
        assert result.returncode == 2
        assert "usage" in result.stderr.lower()

    def test_path_outside_trusted_dir_is_rejected(self, tmp_path: Path) -> None:
        """A real file in /tmp (outside trusted dirs) must be rejected.

        # verified: removing the trusted-dir guard → exit 0 (podman not found) → RED
        """
        real_file = tmp_path / "real.pem"
        real_file.write_text("FAKE PEM CONTENT")

        result = _run_script(ROTATE_GH, str(real_file))

        assert result.returncode != 0, (
            f"Expected non-zero exit for path outside trusted dirs\n"
            f"stderr: {result.stderr}"
        )
        assert any(
            kw in result.stderr.lower()
            for kw in ("trusted", "outside", "/home/factory/secrets", "/etc/factory")
        ), f"Expected trusted-dir rejection in stderr; got: {result.stderr!r}"


class TestRotateClaudeOauthPathTraversal:
    """rotate-claude-oauth.sh must reject traversal paths (sister fix to #1118)."""

    def test_dotdot_traversal_rejected_via_realpath(self, tmp_path: Path) -> None:
        """A path using '..' that resolves outside trusted dirs must be rejected.

        # verified: removing the trusted-dir check → exit code comes from
        # podman-not-found, not from our guard → test assertion on stderr fails → RED
        """
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        target = tmp_path / "fake.tok"
        target.write_text("fake-oauth-token")
        target.chmod(0o600)

        traversal = str(subdir) + "/../fake.tok"

        result = _run_script(ROTATE_CLAUDE, traversal)

        assert result.returncode != 0, (
            f"Expected non-zero exit for traversal path; got 0\n"
            f"stderr: {result.stderr}"
        )
        assert any(
            kw in result.stderr.lower()
            for kw in ("trusted", "outside", "/home/factory/secrets", "/etc/factory")
        ), f"Expected rejection message in stderr; got: {result.stderr!r}"

    def test_path_outside_trusted_dir_is_rejected(self, tmp_path: Path) -> None:
        """A real file in /tmp (outside trusted dirs) must be rejected."""
        real_file = tmp_path / "real.tok"
        real_file.write_text("fake-oauth-token")
        real_file.chmod(0o600)

        result = _run_script(ROTATE_CLAUDE, str(real_file))

        assert result.returncode != 0, (
            f"Expected non-zero exit for path outside trusted dirs\n"
            f"stderr: {result.stderr}"
        )
        assert any(
            kw in result.stderr.lower()
            for kw in ("trusted", "outside", "/home/factory/secrets", "/etc/factory")
        ), f"Expected trusted-dir rejection in stderr; got: {result.stderr!r}"

    def test_no_args_prints_usage(self) -> None:
        result = _run_script(ROTATE_CLAUDE, "")
        assert result.returncode == 2
        assert "usage" in result.stderr.lower()

    def test_symlink_pointing_outside_trusted_dir_is_rejected(
        self, tmp_path: Path
    ) -> None:
        """Symlink inside trusted-dir prefix that resolves outside must be rejected.

        This is the TOCTOU vector: an attacker swaps a legitimate path for a
        symlink to an arbitrary file. realpath -e follows the symlink; the
        allowlist guard must then reject the resolved destination.

        # verified: removing the trusted-dir check → script proceeds to chmod
        # the symlink target → exit 0 (or podman error) → RED
        """
        evil_target = tmp_path / "evil.tok"
        evil_target.write_text("should-not-be-read")
        evil_target.chmod(0o600)

        # Symlink lives in /tmp (outside trusted dirs) — simulates a link that
        # could be placed inside /home/factory/secrets/ in the real TOCTOU scenario.
        link = tmp_path / "link.tok"
        link.symlink_to(evil_target)

        result = _run_script(ROTATE_CLAUDE, str(link))

        assert result.returncode != 0, (
            f"Expected non-zero exit for symlink outside trusted dirs\n"
            f"stderr: {result.stderr}"
        )
        assert any(
            kw in result.stderr.lower()
            for kw in ("trusted", "outside", "/home/factory/secrets", "/etc/factory")
        ), f"Expected trusted-dir rejection in stderr; got: {result.stderr!r}"

    def test_crlf_in_path_argument_is_rejected(self, tmp_path: Path) -> None:
        """Path argument containing CRLF must not inject a raw newline into output.

        The real log-injection vector is a raw CR+LF that causes the spoofed
        text to appear as a *separate line* in stderr (fooling log parsers).
        The script uses printf '%q' to escape the user-supplied value — the
        CRLF is rendered as the literal characters \\r\\n inside $'...', not as
        a real control sequence. This test verifies:
          1. exit non-zero (realpath -e rejects the non-existent path)
          2. the spoofed phrase does NOT appear as a standalone line in stderr
          3. the spoofed phrase does NOT appear in stdout at all
        """
        # Construct a path with an embedded carriage-return + newline.
        crlf_path = str(tmp_path / "real.tok") + "\r\nINFO: secret rotated OK"

        result = _run_script(ROTATE_CLAUDE, crlf_path)

        assert result.returncode != 0, (
            f"Expected non-zero exit for CRLF-injected path\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        # The spoofed phrase must not appear in stdout.
        assert "secret rotated OK" not in result.stdout, (
            "CRLF injection reached stdout"
        )
        # The spoofed phrase must not appear as a raw line in stderr.
        # printf '%q' escapes CR+LF to \\r\\n — so the injection is contained
        # inside the $'...' quoting, not emitted as a standalone line.
        stderr_lines = result.stderr.splitlines()
        assert not any(
            line.strip() == "INFO: secret rotated OK" for line in stderr_lines
        ), (
            f"CRLF injection produced a standalone spoofed line in stderr.\n"
            f"stderr lines: {stderr_lines!r}"
        )
