"""CLI tests for factory user list/show."""

from __future__ import annotations

from typer.testing import CliRunner

from factory.cli.main import factory_app


def test_user_list_empty(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(factory_app, ["user", "list"])
    assert result.exit_code == 0
    assert "no users registered" in result.stdout


def test_user_register(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(
        factory_app, ["user", "register", "tg:user:7377831990"]
    )
    assert result.exit_code == 0
    assert "rx:user:" in result.stdout
    assert "7377831990" in result.stdout


def test_user_link(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
    runner = CliRunner()
    runner.invoke(factory_app, ["user", "register", "tg:user:1"])
    result = runner.invoke(
        factory_app,
        ["user", "link", "tg:user:1", "dc:user:2"],
    )
    assert result.exit_code == 0
    assert "rx:user:" in result.stdout
    show = runner.invoke(factory_app, ["user", "show", "dc:user:2"])
    assert show.exit_code == 0
    assert "tg:user:1" in show.stdout
    assert "dc:user:2" in show.stdout