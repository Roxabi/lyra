"""Tests for run_git_ownership_probe (#1149)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lyra.bootstrap.infra.git_ownership_probe import (
    PROBE_ENV_VAR,
    run_git_ownership_probe,
)

_PATCH_TARGET = "lyra.bootstrap.infra.git_ownership_probe.subprocess.run"


class TestRunGitOwnershipProbeSuccess:
    """Tests for the happy path."""

    def test_success_returns_silently(self, tmp_path: Path) -> None:
        """Returns None when returncode=0 and stderr is empty."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""

        with patch(_PATCH_TARGET, return_value=mock_result) as mock_run:
            result = run_git_ownership_probe(repo_path=str(tmp_path))

        assert result is None
        mock_run.assert_called_once()


class TestRunGitOwnershipProbeFailures:
    """Tests for failure paths that call sys.exit(1)."""

    def test_dubious_ownership_stderr_exits(self, tmp_path: Path) -> None:
        """SystemExit when returncode=0 but stderr contains 'dubious ownership'."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = "fatal: detected dubious ownership in repository at '/r'"

        with patch(_PATCH_TARGET, return_value=mock_result):
            with pytest.raises(SystemExit):
                run_git_ownership_probe(repo_path=str(tmp_path))

    def test_non_zero_exit_exits(self, tmp_path: Path) -> None:
        """SystemExit when subprocess returns non-zero exit code."""
        mock_result = MagicMock()
        mock_result.returncode = 128
        mock_result.stderr = "fatal: not a git repository"

        with patch(_PATCH_TARGET, return_value=mock_result):
            with pytest.raises(SystemExit):
                run_git_ownership_probe(repo_path=str(tmp_path))

    def test_missing_target_path_exits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SystemExit when target directory does not exist; subprocess never called."""
        monkeypatch.delenv(PROBE_ENV_VAR, raising=False)

        with patch(_PATCH_TARGET) as mock_run:
            with pytest.raises(SystemExit):
                run_git_ownership_probe(repo_path="/nonexistent/lyra-probe-test")

        mock_run.assert_not_called()


class TestRunGitOwnershipProbePathResolution:
    """Tests for path resolution order."""

    def test_env_override_resolves_before_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When LYRA_OWNERSHIP_PROBE_PATH is set, it is used when no arg given."""
        monkeypatch.setenv(PROBE_ENV_VAR, str(tmp_path))

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""

        with patch(_PATCH_TARGET, return_value=mock_result) as mock_run:
            run_git_ownership_probe()

        mock_run.assert_called_once()
        assert mock_run.call_args.kwargs["cwd"] == str(tmp_path)

    def test_explicit_arg_resolves_before_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Explicit repo_path wins over LYRA_OWNERSHIP_PROBE_PATH env var."""
        env_dir = tmp_path / "env_dir"
        env_dir.mkdir()
        explicit_dir = tmp_path / "explicit_dir"
        explicit_dir.mkdir()

        monkeypatch.setenv(PROBE_ENV_VAR, str(env_dir))

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""

        with patch(_PATCH_TARGET, return_value=mock_result) as mock_run:
            run_git_ownership_probe(repo_path=str(explicit_dir))

        mock_run.assert_called_once()
        assert mock_run.call_args.kwargs["cwd"] == str(explicit_dir)
