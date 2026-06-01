"""E2E tests for `lyra bot init` (issue #1414, T9)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lyra.cli import lyra_app as app
from lyra.core.agent.bot_models import BotRow
from lyra.infrastructure.stores.bot_store import BotStore
from tests.helpers.bot_cli import write_bot_toml
from tests.helpers.bot_store import db_get, db_upsert

# typer.testing.CliRunner merges stderr into result.output by default.
runner = CliRunner()


# ---------------------------------------------------------------------------
# TestBotInitHelp
# ---------------------------------------------------------------------------


class TestBotInitHelp:
    """`lyra bot init --help`"""

    def test_help_shows_force_flag(self) -> None:
        # NO_COLOR=1 disables Rich's ANSI styling so '--force' is a contiguous
        # literal in result.output instead of being split by escape codes.
        result = runner.invoke(
            app,
            ["bot", "init", "--help"],
            env={"COLUMNS": "200", "NO_COLOR": "1", "TERM": "dumb"},
        )
        assert result.exit_code == 0
        assert "--force" in result.output


# ---------------------------------------------------------------------------
# TestBotInitErrors
# ---------------------------------------------------------------------------


class TestBotInitErrors:
    """Error paths: missing config, invalid TOML."""

    def test_no_config_toml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange — empty tmp_path, no config.toml anywhere
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        monkeypatch.chdir(tmp_path)

        # Act
        result = runner.invoke(app, ["bot", "init"])

        # Assert
        assert result.exit_code != 0
        assert "config" in result.output.lower()

    def test_invalid_toml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange — write broken TOML
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        write_bot_toml(tmp_path, "this is [[[not valid")

        # Act
        result = runner.invoke(app, ["bot", "init"])

        # Assert
        assert result.exit_code != 0
        assert "parse" in result.output.lower() or "Error" in result.output

    def test_no_bot_sections(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Spec edge case: config.toml with no bot arrays → exit 0, 0 seeded.
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        write_bot_toml(tmp_path, "[server]\nfoo = 1\n")  # valid TOML, no bot arrays

        result = runner.invoke(app, ["bot", "init"])

        assert result.exit_code == 0, result.output
        assert "0 seeded" in result.output


# ---------------------------------------------------------------------------
# TestBotInitSeed
# ---------------------------------------------------------------------------


class TestBotInitSeed:
    """Happy path: seed from config.toml."""

    def test_seed_single_bot(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        write_bot_toml(
            tmp_path,
            '[[telegram.bots]]\nbot_id="main"\nagent="a"\n',
        )

        # Act
        result = runner.invoke(app, ["bot", "init"])

        # Assert — CLI success
        assert result.exit_code == 0, result.output
        assert "seeded" in result.output

        # Assert — DB has the row
        db_path = tmp_path / "config.db"
        row = db_get(db_path, "telegram", "main")
        assert row is not None
        assert row.agent == "a"

    def test_idempotent_re_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        write_bot_toml(
            tmp_path,
            '[[telegram.bots]]\nbot_id="main"\nagent="a"\n',
        )

        # Act — first run
        result1 = runner.invoke(app, ["bot", "init"])
        assert result1.exit_code == 0

        # Capture updated_at after first run
        db_path = tmp_path / "config.db"
        first_row = db_get(db_path, "telegram", "main")
        assert first_row is not None
        first_updated_at = first_row.updated_at

        # Act — second run (idempotent)
        result2 = runner.invoke(app, ["bot", "init"])

        # Assert — output counts
        assert result2.exit_code == 0, result2.output
        assert "skipped" in result2.output
        assert "0 seeded" in result2.output
        assert "1 skipped" in result2.output

        # Assert — DB row was NOT rewritten (updated_at unchanged)
        second_row = db_get(db_path, "telegram", "main")
        assert second_row is not None
        assert second_row.updated_at == first_updated_at

    def test_force_overwrite(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        write_bot_toml(
            tmp_path,
            '[[telegram.bots]]\nbot_id="main"\nagent="a"\n',
        )

        # Act — seed once
        result1 = runner.invoke(app, ["bot", "init"])
        assert result1.exit_code == 0

        # Arrange — manually mutate DB row
        db_path = tmp_path / "config.db"
        db_upsert(db_path, BotRow(platform="telegram", bot_id="main", agent="mutated"))

        # Act — force re-seed
        result2 = runner.invoke(app, ["bot", "init", "--force"])

        # Assert
        assert result2.exit_code == 0, result2.output
        assert "seeded" in result2.output

        row = db_get(db_path, "telegram", "main")
        assert row is not None
        assert row.agent == "a"

    def test_force_on_empty_db(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # --force on a fresh DB with no prior rows must still seed normally.
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        write_bot_toml(
            tmp_path,
            '[[telegram.bots]]\nbot_id="main"\nagent="a"\n',
        )

        result = runner.invoke(app, ["bot", "init", "--force"])

        assert result.exit_code == 0, result.output
        assert "1 seeded" in result.output

    def test_merge_multi_section(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Exercises merge semantics: conflicting scalars (last-wins) and
        # list deduplication across [[telegram.bots]] and [[auth.telegram_bots]].
        # _add_entries order: telegram.bots first, auth.telegram_bots last →
        # auth.telegram_bots scalar values win.
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        write_bot_toml(
            tmp_path,
            "[[telegram.bots]]\n"
            'bot_id="main"\n'
            'agent="a"\n'
            'owner_users=["alice", "bob"]\n\n'
            "[[auth.telegram_bots]]\n"
            'bot_id="main"\n'
            'agent="b"\n'
            'owner_users=["bob", "charlie"]\n',
        )

        result = runner.invoke(app, ["bot", "init"])

        assert result.exit_code == 0, result.output
        assert "1 seeded" in result.output  # single merged row

        db_path = tmp_path / "config.db"
        row = db_get(db_path, "telegram", "main")
        assert row is not None
        assert row.agent == "b"  # auth.telegram_bots wins (last section)
        assert set(row.owner_users) == {"alice", "bob", "charlie"}  # concat + dedup

    def test_default_values(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange — minimal TOML that omits auto_thread and thread_hot_hours
        # so that dataclass defaults apply (conservative: False / 24).
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        write_bot_toml(
            tmp_path,
            '[[telegram.bots]]\nagent="a"\n',
        )

        # Act
        result = runner.invoke(app, ["bot", "init"])

        # Assert
        assert result.exit_code == 0, result.output
        assert "1 seeded" in result.output

        db_path = tmp_path / "config.db"
        row = db_get(db_path, "telegram", "main")
        assert row is not None
        assert row.agent == "a"
        assert row.default_trust == "blocked"
        assert row.auto_thread is False
        assert row.thread_hot_hours == 24


# ---------------------------------------------------------------------------
# TestBotInitValidation
# ---------------------------------------------------------------------------


class TestBotInitValidation:
    """Input validation: invalid bot_id / platform are skipped with error."""

    def test_invalid_bot_id_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # bot_id with path-traversal characters must be rejected and counted as error.
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        write_bot_toml(
            tmp_path,
            '[[telegram.bots]]\nbot_id="../../evil"\nagent="a"\n',
        )

        result = runner.invoke(app, ["bot", "init"])

        # spec SC-10: validation errors → exit 1
        assert result.exit_code == 1, result.output  # 1 error → exits 1
        assert "error" in result.output.lower()
        # DB must have 0 rows — invalid entry was not persisted
        db_path = tmp_path / "config.db"
        row = db_get(db_path, "telegram", "../../evil")
        assert row is None

        async def _get_all() -> list[BotRow]:
            store = BotStore(db_path=str(db_path))
            await store.connect()
            rows = store.get_all()
            await store.close()
            return rows

        assert asyncio.run(_get_all()) == []
