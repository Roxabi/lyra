"""End-to-end git attribution test for prepare-commit-msg hook (#1150).

Verifies that given the correct env vars, the hook + git produce the
expected committer identity and trailers — the reviewer-visible signal
from spec SC#6.

Scope: this test exercises only the env → git commit → git log path.
It does NOT exercise the NATS / CliPool path (that is covered by T12).
The integration here is: env vars set → hook appended trailers and (when
GIT_COMMITTER_* are also set) git records the correct committer.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def _find_hook() -> Path:
    """Locate deploy/factory-gh/hooks/prepare-commit-msg relative to repo root."""
    # Walk up from this file to the repo root (contains pyproject.toml).
    for parent in Path(__file__).resolve().parents:
        hook = parent / "deploy" / "factory-gh" / "hooks" / "prepare-commit-msg"
        if hook.exists():
            return hook
    raise FileNotFoundError(
        "prepare-commit-msg hook not found — searched from "
        f"{Path(__file__).resolve()} up to fs root"
    )


def _init_repo(path: Path) -> None:
    """Initialise a git repo with a user identity."""
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "test-committer"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@example.com"],
        check=True,
        capture_output=True,
    )
    # Wire hooksPath to the production hook directory (not a copy of the hook).
    hook_dir = _find_hook().parent
    subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "config",
            "core.hooksPath",
            str(hook_dir),
        ],
        check=True,
        capture_output=True,
    )


def _git_log(repo: Path, fmt: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "log", "-1", f"--format={fmt}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


class TestClipoolIdentityE2E:
    """End-to-end attribution via prepare-commit-msg hook (#1150 SC6)."""

    def test_full_mode_attribution(self, tmp_path: Path) -> None:
        """Full mode: GIT_COMMITTER_* + FACTORY_* env → committer + trailers correct.

        Given env:
          GIT_COMMITTER_NAME=agent-X
          GIT_COMMITTER_EMAIL=x@y.com
          FACTORY_AGENT=agent-X
          FACTORY_SESSION_ID=S-e2e

        Expected git log format:
          agent-X|x@y.com|S-e2e|agent-X
        """
        # Arrange
        repo = tmp_path / "full-mode-repo"
        _init_repo(repo)

        identity_env = {
            "GIT_COMMITTER_NAME": "agent-X",
            "GIT_COMMITTER_EMAIL": "x@y.com",
            "FACTORY_AGENT": "agent-X",
            "FACTORY_SESSION_ID": "S-e2e",
        }

        # Act — commit with identity env vars set
        subprocess.run(
            ["git", "-C", str(repo), "commit", "--allow-empty", "-m", "full-mode"],
            check=True,
            capture_output=True,
            env={**_base_env(), **identity_env},
        )

        # Assert
        log_line = _git_log(
            repo,
            "%cn|%ce"
            "|%(trailers:key=Lyra-Session-Id,valueonly)"
            "|%(trailers:key=Lyra-Agent,valueonly)",
        )
        # Strip any trailing newlines in trailer values (git may append \n)
        parts = [p.strip() for p in log_line.split("|")]
        assert parts == ["agent-X", "x@y.com", "S-e2e", "agent-X"], (
            f"Unexpected git log output: {log_line!r}"
        )

    def test_trailers_only_mode_attribution(self, tmp_path: Path) -> None:
        """Trailers-only mode: FACTORY_* only → trailers correct; committer unchanged.

        Given env:
          FACTORY_AGENT=agent-X
          FACTORY_SESSION_ID=S-trailers
          (NO GIT_COMMITTER_* vars)

        The committer name/email will be whatever git's user.* config says
        (test-committer / test@example.com) — we assert ONLY the trailers.
        """
        # Arrange
        repo = tmp_path / "trailers-only-repo"
        _init_repo(repo)

        trailers_env = {
            "FACTORY_AGENT": "agent-X",
            "FACTORY_SESSION_ID": "S-trailers",
        }

        # Act — commit with FACTORY_* vars but no GIT_COMMITTER_* vars
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "commit",
                "--allow-empty",
                "-m",
                "trailers-only",
            ],
            check=True,
            capture_output=True,
            env={**_base_env(), **trailers_env},
        )

        # Assert — only the trailer half; committer is not asserted
        session_trailer = _git_log(
            repo,
            "%(trailers:key=Lyra-Session-Id,valueonly)",
        ).strip()
        agent_trailer = _git_log(
            repo,
            "%(trailers:key=Lyra-Agent,valueonly)",
        ).strip()

        assert session_trailer == "S-trailers", (
            f"Lyra-Session-Id trailer wrong: {session_trailer!r}"
        )
        assert agent_trailer == "agent-X", (
            f"Lyra-Agent trailer wrong: {agent_trailer!r}"
        )


def _base_env() -> dict[str, str]:
    """Minimal env for git subprocess (PATH, HOME, GIT_CONFIG_NOSYSTEM)."""
    import os

    return {
        "HOME": os.environ.get("HOME", "/tmp"),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "test-author",
        "GIT_AUTHOR_EMAIL": "author@example.com",
        # Prevent date-based flakiness in commit metadata
        "GIT_AUTHOR_DATE": "2026-05-19T00:00:00+00:00",
        "GIT_COMMITTER_DATE": "2026-05-19T00:00:00+00:00",
    }
