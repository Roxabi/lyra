"""Tests for numeric TOML values in _BotSeedEntry — thread_hot_hours etc."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from factory.agent_cmd.bots.init import _BotSeedEntry, _merge_bots
from factory.cli import factory_app as app
from tests.helpers.bot_cli import write_bot_toml
from tests.helpers.bot_store import db_get


class TestBotSeedEntryNumericFields:
    def test_thread_hot_hours_int_accepted(self) -> None:
        entry = _BotSeedEntry.model_validate(
            {"bot_id": "main", "thread_hot_hours": 6}
        )
        assert entry.thread_hot_hours == 6

    def test_webhook_enabled_bool_accepted(self) -> None:
        entry = _BotSeedEntry.model_validate(
            {"bot_id": "main", "webhook_enabled": True}
        )
        assert entry.webhook_enabled is True


class TestMergeBotsNumericFields:
    def test_merge_bots_thread_hot_hours(self) -> None:
        raw = {
            "discord": {
                "bots": [{"bot_id": "main", "thread_hot_hours": 12}]
            }
        }
        rows, errors = _merge_bots(raw)
        assert errors == 0
        assert rows[0].thread_hot_hours == 12


class TestBotInitIdempotent:
    def test_idempotent_re_run_skips_row(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        write_bot_toml(
            tmp_path,
            '[[telegram.bots]]\nbot_id="main"\nagent="a"\nthread_hot_hours=6\n',
        )

        runner = CliRunner()
        result1 = runner.invoke(app, ["bot", "init"])
        assert result1.exit_code == 0, result1.output
        assert "seeded" in result1.output

        db_path = tmp_path / "config.db"
        row = db_get(db_path, "telegram", "main")
        assert row is not None
        assert row.thread_hot_hours == 6

        result2 = runner.invoke(app, ["bot", "init"])
        assert result2.exit_code == 0, result2.output
        assert "skipped" in result2.output
        assert "1 skipped" in result2.output
        assert "0 seeded" in result2.output