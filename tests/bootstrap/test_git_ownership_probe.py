"""Tests for run_git_ownership_probe (#1149, #1718)."""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from factory.bootstrap.infra.git_ownership_probe import (
    PROBE_ENV_VAR,
    run_git_ownership_probe,
)

_LOGGER_NAME = "factory.bootstrap.infra.git_ownership_probe"

_PATCH_TARGET = "factory.bootstrap.infra.git_ownership_probe.subprocess.run"


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
        """SystemExit(1) when returncode=0 but stderr contains 'dubious ownership'."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = "fatal: detected dubious ownership in repository at '/r'"

        with patch(_PATCH_TARGET, return_value=mock_result):
            with pytest.raises(SystemExit) as exc_info:
                run_git_ownership_probe(repo_path=str(tmp_path))
        assert exc_info.value.code == 1

    def test_non_zero_exit_exits(self, tmp_path: Path) -> None:
        """SystemExit(1) when subprocess returns non-zero exit code."""
        mock_result = MagicMock()
        mock_result.returncode = 128
        mock_result.stderr = "fatal: not a git repository"

        with patch(_PATCH_TARGET, return_value=mock_result):
            with pytest.raises(SystemExit) as exc_info:
                run_git_ownership_probe(repo_path=str(tmp_path))
        assert exc_info.value.code == 1

    def test_missing_target_path_exits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SystemExit(1) when target dir does not exist; subprocess never called."""
        monkeypatch.delenv(PROBE_ENV_VAR, raising=False)

        with patch(_PATCH_TARGET) as mock_run:
            with pytest.raises(SystemExit) as exc_info:
                run_git_ownership_probe(repo_path="/nonexistent/lyra-probe-test")
        assert exc_info.value.code == 1
        mock_run.assert_not_called()

    def test_subprocess_timeout_exits(self, tmp_path: Path) -> None:
        """SystemExit(1) when subprocess.run raises TimeoutExpired."""
        with patch(
            _PATCH_TARGET,
            side_effect=subprocess.TimeoutExpired(
                cmd=["git", "rev-parse", "HEAD"], timeout=5
            ),
        ) as mock_run:
            with pytest.raises(SystemExit) as exc_info:
                run_git_ownership_probe(repo_path=str(tmp_path))
        assert exc_info.value.code == 1
        mock_run.assert_called_once()

    def test_git_binary_not_found_exits(self, tmp_path: Path) -> None:
        """SystemExit(1) when subprocess.run raises FileNotFoundError (git absent)."""
        with patch(
            _PATCH_TARGET,
            side_effect=FileNotFoundError("git not found"),
        ) as mock_run:
            with pytest.raises(SystemExit) as exc_info:
                run_git_ownership_probe(repo_path=str(tmp_path))
        assert exc_info.value.code == 1
        mock_run.assert_called_once()


class TestRunGitOwnershipProbePathResolution:
    """Tests for path resolution order."""

    def test_env_override_resolves_before_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When FACTORY_OWNERSHIP_PROBE_PATH is set, it is used when no arg given."""
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
        """Explicit repo_path wins over FACTORY_OWNERSHIP_PROBE_PATH env var."""
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


class TestRunGitOwnershipProbeSymlink:
    """Tests for symlink detection introduced in #1718."""

    def test_dangling_absolute_symlink_emits_relative_hint_and_exits(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Dangling absolute symlink → log.error with RELATIVE hint + SystemExit(1).

        The symlink exists on the filesystem but its absolute target does not,
        which is the cross-namespace container scenario.
        """
        # Arrange — create a symlink whose absolute target does not exist
        link = tmp_path / "factory-bridge"
        nonexistent_absolute = "/tmp/roxabi-probe-does-not-exist-1718"
        os.symlink(nonexistent_absolute, str(link))
        assert os.path.islink(str(link)), "sanity: symlink must exist"
        assert not os.path.isdir(str(link)), "sanity: target must not resolve"

        # Act + Assert exit
        with caplog.at_level(logging.ERROR, logger=_LOGGER_NAME):
            with pytest.raises(SystemExit) as exc_info:
                run_git_ownership_probe(repo_path=str(link))

        assert exc_info.value.code == 1

        # Assert remediation hint is present in at least one error record
        error_messages = [
            r.getMessage() for r in caplog.records if r.levelno == logging.ERROR
        ]
        assert any("RELATIVE" in msg for msg in error_messages), (
            f"Expected 'RELATIVE' remediation hint in error log, got: {error_messages}"
        )
        assert any("ln -sfr" in msg for msg in error_messages), (
            f"Expected 'ln -sfr' remediation hint in error log, got: {error_messages}"
        )

    def test_dangling_absolute_symlink_does_not_emit_generic_missing_message(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Dangling absolute symlink must NOT produce the generic 'does not exist' text.

        Negative guard: verifies the symlink branch fires rather than the fallthrough.
        """
        # Arrange
        link = tmp_path / "factory-bridge-2"
        os.symlink("/tmp/roxabi-probe-absent-1718b", str(link))

        # Act
        with caplog.at_level(logging.ERROR, logger=_LOGGER_NAME):
            with pytest.raises(SystemExit):
                run_git_ownership_probe(repo_path=str(link))

        error_messages = [
            r.getMessage() for r in caplog.records if r.levelno == logging.ERROR
        ]
        assert not any("does not exist" in msg for msg in error_messages), (
            "Dangling symlink must not trigger the generic 'does not exist' error; "
            f"got: {error_messages}"
        )

    def test_relative_symlink_to_real_dir_passes(
        self,
        tmp_path: Path,
    ) -> None:
        """Relative symlink that resolves to an actual directory → probe succeeds.

        subprocess.run is mocked to return success so the git binary is not needed.
        """
        # Arrange — real_dir exists; link is a relative symlink inside tmp_path
        real_dir = tmp_path / "real_repo"
        real_dir.mkdir()
        link = tmp_path / "factory-rel-link"
        # relative target: just the directory name (same parent)
        os.symlink("real_repo", str(link))
        assert os.path.isdir(str(link)), "sanity: relative symlink must resolve"

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""

        # Act + Assert — no exception
        with patch(_PATCH_TARGET, return_value=mock_result) as mock_run:
            result = run_git_ownership_probe(repo_path=str(link))

        assert result is None
        mock_run.assert_called_once()

    def test_genuine_missing_non_symlink_path_uses_generic_message(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A plain missing path (not a symlink) → generic 'does not exist' error,
        NOT the cross-namespace symlink hint.

        Verifies the original pre-#1718 path is unchanged.
        """
        # Arrange — path that does not exist and is not a symlink
        monkeypatch.delenv(PROBE_ENV_VAR, raising=False)
        missing = str(tmp_path / "genuinely_absent_directory")
        assert not os.path.exists(missing), "sanity: path must not exist"
        assert not os.path.islink(missing), "sanity: path must not be a symlink"

        # Act
        with caplog.at_level(logging.ERROR, logger=_LOGGER_NAME):
            with pytest.raises(SystemExit) as exc_info:
                run_git_ownership_probe(repo_path=missing)

        assert exc_info.value.code == 1

        error_messages = [
            r.getMessage() for r in caplog.records if r.levelno == logging.ERROR
        ]
        assert any("does not exist" in msg for msg in error_messages), (
            f"Expected generic 'does not exist' error, got: {error_messages}"
        )
        assert not any("RELATIVE" in msg for msg in error_messages), (
            "Generic missing-path must NOT produce the symlink remediation hint; "
            f"got: {error_messages}"
        )

    def test_symlink_to_regular_file_emits_relative_hint_and_exits(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Symlink pointing to a regular file (not a dir) → RELATIVE hint + exit(1).

        os.path.islink(target) is True but os.path.isdir(target) is False,
        so the same branch fires as for dangling absolute symlinks.
        """
        # Arrange — symlink → regular file (valid target, but not a directory)
        real_file = tmp_path / "not_a_dir.txt"
        real_file.write_text("content", encoding="utf-8")
        link = tmp_path / "factory-file-link"
        os.symlink(str(real_file), str(link))
        assert os.path.islink(str(link)), "sanity: symlink must exist"
        assert not os.path.isdir(str(link)), "sanity: target is a file, not a dir"

        # Act
        with caplog.at_level(logging.ERROR, logger=_LOGGER_NAME):
            with pytest.raises(SystemExit) as exc_info:
                run_git_ownership_probe(repo_path=str(link))

        assert exc_info.value.code == 1

        error_messages = [
            r.getMessage() for r in caplog.records if r.levelno == logging.ERROR
        ]
        assert any("RELATIVE" in msg for msg in error_messages), (
            f"Expected 'RELATIVE' remediation hint in error log, got: {error_messages}"
        )
