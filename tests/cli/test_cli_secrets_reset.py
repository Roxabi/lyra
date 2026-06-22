"""Tests for `factory secrets reset` disaster recovery CLI."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from factory.cli import factory_app
from factory.cli.secrets_reset import (
    build_reset_plan,
    confirm_reset,
    factory_repo_root,
    run_secrets_reset,
)

runner = CliRunner()


def _fake_repo(tmp_path: Path) -> Path:
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    (deploy / "install.sh").write_text("#!/bin/sh\n")
    (deploy / "nats").mkdir()
    (deploy / "nats" / "acl-matrix.json").write_text('{"version":"1","identities":{}}')
    return tmp_path


class TestResetPlan:
    def test_build_plan_without_converge(self) -> None:
        plan = build_reset_plan(converge=False)
        assert "genkeys --regenerate" in plan.steps[0]
        assert "install.sh" in plan.steps[1]
        assert "manual" in plan.steps[2]

    def test_build_plan_with_converge(self) -> None:
        plan = build_reset_plan(converge=True)
        assert plan.steps[-1].startswith("make converge")


class TestConfirmReset:
    def test_assume_yes_skips_prompt(self) -> None:
        confirm_reset(assume_yes=True)

    def test_non_tty_without_yes_exits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        with pytest.raises(SystemExit, match="Refusing destructive reset"):
            confirm_reset(assume_yes=False)

    def test_tty_decline_aborts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("builtins.input", lambda _: "n")
        with pytest.raises(SystemExit, match="Aborted"):
            confirm_reset(assume_yes=False)


class TestFactoryRepoRoot:
    def test_from_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = _fake_repo(tmp_path)
        monkeypatch.setenv("ROXABI_FACTORY_REPO", str(root))
        assert factory_repo_root() == root.resolve()

    def test_invalid_env_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_REPO", str(tmp_path))
        with pytest.raises(FileNotFoundError, match="ROXABI_FACTORY_REPO invalid"):
            factory_repo_root()


class TestRunSecretsReset:
    def test_dry_run_prints_steps(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        root = _fake_repo(tmp_path)
        plan = run_secrets_reset(dry_run=True, repo_root=root, converge=True)
        out = capsys.readouterr().out
        assert str(root) in out
        assert len(plan.steps) == 3
        for step in plan.steps:
            assert step.split(" ", 1)[-1] in out or step in out

    def test_executes_regenerate_install_converge(self, tmp_path: Path) -> None:
        root = _fake_repo(tmp_path)
        calls: list[list[str]] = []

        def fake_runner(cmd: list[str], **kwargs: object) -> MagicMock:
            calls.append(cmd)
            proc = MagicMock(spec=subprocess.CompletedProcess)
            proc.returncode = 0
            return proc

        regen_called: list[bool] = []

        def fake_regen() -> None:
            regen_called.append(True)

        with patch("factory.cli.secrets_reset.confirm_reset"):
            run_secrets_reset(
                yes=True,
                converge=True,
                repo_root=root,
                run_regenerate=fake_regen,
                runner=fake_runner,
            )

        assert regen_called == [True]
        assert calls[0][0].endswith("install.sh")
        assert calls[0][1:] == ["--force", "--secrets-only"]
        assert calls[1] == ["make", "converge"]


class TestSecretsResetCli:
    def test_dry_run_exit_zero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _fake_repo(tmp_path)
        monkeypatch.setenv("ROXABI_FACTORY_REPO", str(root))
        result = runner.invoke(factory_app, ["secrets", "reset", "--dry-run"])
        assert result.exit_code == 0
        assert "genkeys --regenerate" in result.output

    def test_subprocess_failure_propagates_exit_code(self, tmp_path: Path) -> None:
        root = _fake_repo(tmp_path)

        def boom(cmd: list[str], **kwargs: object) -> None:
            raise subprocess.CalledProcessError(7, cmd)

        with (
            patch("factory.cli.secrets_reset.factory_repo_root", return_value=root),
            patch("factory.cli.secrets_reset.confirm_reset"),
            patch("factory.cli.secrets_reset.run_nkeys_regenerate"),
            patch("factory.cli.secrets_reset.run_install_secrets", side_effect=boom),
        ):
            result = runner.invoke(factory_app, ["secrets", "reset", "--yes"])

        assert result.exit_code == 7
        assert "Command failed" in result.output

    def test_missing_repo_exit_two(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ROXABI_FACTORY_REPO", raising=False)
        with patch(
            "factory.cli.secrets_reset.factory_repo_root",
            side_effect=FileNotFoundError("no repo"),
        ):
            result = runner.invoke(factory_app, ["secrets", "reset", "--dry-run"])
        assert result.exit_code == 2
        assert "no repo" in result.output
