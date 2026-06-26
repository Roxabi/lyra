"""Tests for the ADR-090 §7 operator CLI: `factory agent grant/revoke/auth list`.

Each test isolates both ``config.db`` and ``auth.db`` under a pytest ``tmp_path``
via ``ROXABI_FACTORY_DIR`` (the env var ``factory_data_dir()`` honours), so grants
written by ``grant`` land in a throwaway ``auth.db`` and are read back by
``auth list`` / cleared by ``revoke``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from factory.cli import agent_app

runner = CliRunner()

_AGENT = "lyra"
_USER = "tg:user:7377831990"
_ROLE = "dc:role:1234"


def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point factory_data_dir() at a throwaway dir for this test."""
    monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))


class TestAgentGrant:
    def test_grant_user_then_list_shows_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _isolate(tmp_path, monkeypatch)
        granted = runner.invoke(agent_app, ["grant", _AGENT, "--user", _USER])
        assert granted.exit_code == 0, granted.output
        assert "granted use" in granted.output.lower()

        listed = runner.invoke(agent_app, ["auth", "list", _AGENT])
        assert listed.exit_code == 0, listed.output
        assert "rx:user:" in listed.output
        assert "user" in listed.output.lower()

    def test_grant_role(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _isolate(tmp_path, monkeypatch)
        result = runner.invoke(agent_app, ["grant", _AGENT, "--role", _ROLE])
        assert result.exit_code == 0, result.output
        listed = runner.invoke(agent_app, ["auth", "list", _AGENT])
        assert _ROLE in listed.output
        assert "role" in listed.output.lower()

    def test_grant_is_idempotent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _isolate(tmp_path, monkeypatch)
        for _ in range(2):
            result = runner.invoke(agent_app, ["grant", _AGENT, "--user", _USER])
            assert result.exit_code == 0, result.output
        listed = runner.invoke(agent_app, ["auth", "list", _AGENT])
        # One grant row only — the UNIQUE constraint upserts rather than dupes.
        assert listed.output.count("rx:user:") == 1

    def test_grant_requires_exactly_one_subject(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _isolate(tmp_path, monkeypatch)
        neither = runner.invoke(agent_app, ["grant", _AGENT])
        assert neither.exit_code == 1
        assert "exactly one" in neither.output.lower()

        both = runner.invoke(
            agent_app, ["grant", _AGENT, "--user", _USER, "--role", _ROLE]
        )
        assert both.exit_code == 1
        assert "exactly one" in both.output.lower()

    def test_grant_empty_principal_id_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _isolate(tmp_path, monkeypatch)
        result = runner.invoke(agent_app, ["grant", _AGENT, "--user", ""])
        assert result.exit_code == 1
        assert "non-empty" in result.output.lower()

    def test_grant_invalid_capability_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _isolate(tmp_path, monkeypatch)
        result = runner.invoke(
            agent_app, ["grant", _AGENT, "--user", _USER, "--capability", "bogus"]
        )
        assert result.exit_code == 1
        assert "invalid capability" in result.output.lower()


class TestAgentRevoke:
    def test_revoke_existing_grant(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _isolate(tmp_path, monkeypatch)
        runner.invoke(agent_app, ["grant", _AGENT, "--user", _USER])
        revoked = runner.invoke(agent_app, ["revoke", _AGENT, "--user", _USER])
        assert revoked.exit_code == 0, revoked.output
        assert "revoked use" in revoked.output.lower()

        listed = runner.invoke(agent_app, ["auth", "list", _AGENT])
        assert _USER not in listed.output

    def test_revoke_absent_grant_reports_no_op(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _isolate(tmp_path, monkeypatch)
        result = runner.invoke(agent_app, ["revoke", _AGENT, "--user", _USER])
        assert result.exit_code == 0, result.output
        assert "no use grant" in result.output.lower()


class TestAgentAuthList:
    def test_list_empty_agent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _isolate(tmp_path, monkeypatch)
        result = runner.invoke(agent_app, ["auth", "list", _AGENT])
        assert result.exit_code == 0, result.output
        assert "no grants" in result.output.lower()
