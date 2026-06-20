"""Tests for factory.paths — canonical factory data directory helpers."""

from __future__ import annotations

from pathlib import Path

from factory.paths import factory_data_dir, factory_turns_db_path


class TestFactoryTurnsDbPath:
    def test_default_under_turn_writer(self, monkeypatch) -> None:
        monkeypatch.delenv("FACTORY_TURNS_DB", raising=False)
        monkeypatch.setenv("ROXABI_FACTORY_DIR", "/data/factory")

        assert factory_turns_db_path() == Path("/data/factory/turn-writer/turns.db")

    def test_env_override(self, monkeypatch) -> None:
        monkeypatch.setenv("FACTORY_TURNS_DB", "/custom/turns.db")

        assert factory_turns_db_path() == Path("/custom/turns.db")

    def test_default_uses_factory_data_dir(self, monkeypatch) -> None:
        monkeypatch.delenv("FACTORY_TURNS_DB", raising=False)
        monkeypatch.delenv("ROXABI_FACTORY_DIR", raising=False)

        expected = factory_data_dir() / "turn-writer" / "turns.db"
        assert factory_turns_db_path() == expected
