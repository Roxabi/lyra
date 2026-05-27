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

runner = CliRunner()


def _db_get(db_path: Path, platform: str, bot_id: str) -> BotRow | None:
    """Read a bot row from DB synchronously."""

    async def _run() -> BotRow | None:
        store = BotStore(db_path=str(db_path))
        await store.connect()
        row = store.get(platform, bot_id)
        await store.close()
        return row

    return asyncio.run(_run())


def _db_upsert(db_path: Path, row: BotRow) -> None:
    """Upsert a bot row into DB synchronously."""

    async def _run() -> None:
        store = BotStore(db_path=str(db_path))
        await store.connect()
        await store.upsert(row)
        await store.close()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# TestBotInitHelp
# ---------------------------------------------------------------------------


class TestBotInitHelp:
    """`lyra bot init --help`"""

    def test_help_shows_force_flag(self) -> None:
        result = runner.invoke(app, ["bot", "init", "--help"], env={"COLUMNS": "80"})
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
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
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
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
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
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
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
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
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
        row = _db_get(db_path, "telegram", "main")
        assert row is not None
        assert row.agent == "a"

    def test_idempotent_re_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
        write_bot_toml(
            tmp_path,
            '[[telegram.bots]]\nbot_id="main"\nagent="a"\n',
        )

        # Act — first run
        result1 = runner.invoke(app, ["bot", "init"])
        assert result1.exit_code == 0

        # Act — second run (idempotent)
        result2 = runner.invoke(app, ["bot", "init"])

        # Assert
        assert result2.exit_code == 0, result2.output
        assert "skipped" in result2.output
        assert "0 seeded" in result2.output
        assert "1 skipped" in result2.output

    def test_force_overwrite(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
        write_bot_toml(
            tmp_path,
            '[[telegram.bots]]\nbot_id="main"\nagent="a"\n',
        )

        # Act — seed once
        result1 = runner.invoke(app, ["bot", "init"])
        assert result1.exit_code == 0

        # Arrange — manually mutate DB row
        db_path = tmp_path / "config.db"
        _db_upsert(db_path, BotRow(platform="telegram", bot_id="main", agent="mutated"))

        # Act — force re-seed
        result2 = runner.invoke(app, ["bot", "init", "--force"])

        # Assert
        assert result2.exit_code == 0, result2.output
        assert "seeded" in result2.output

        row = _db_get(db_path, "telegram", "main")
        assert row is not None
        assert row.agent == "a"

    def test_merge_multi_section(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Exercises merge semantics: conflicting scalars (last-wins) and
        # list deduplication across [[telegram.bots]] and [[auth.telegram_bots]].
        # _add_entries order: telegram.bots first, auth.telegram_bots last →
        # auth.telegram_bots scalar values win.
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
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
        row = _db_get(db_path, "telegram", "main")
        assert row is not None
        assert row.agent == "b"  # auth.telegram_bots wins (last section)
        assert set(row.owner_users) == {"alice", "bob", "charlie"}  # concat + dedup

    def test_default_values(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange — minimal TOML with only agent
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
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
        row = _db_get(db_path, "telegram", "main")
        assert row is not None
        assert row.agent == "a"
        assert row.default_trust == "blocked"
        assert row.auto_thread is True
        assert row.thread_hot_hours == 36
