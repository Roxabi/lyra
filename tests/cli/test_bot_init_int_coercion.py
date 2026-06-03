"""Tests for int→str coercion in _BotSeedEntry.owner_users/trusted_users — #1718 T16.

Covers:
  - int owner_users=[7377831990] → coerced to ['7377831990'] via model_validate
  - mixed [123, "456"] → ['123', '456']
  - _merge_bots with int IDs → seeded BotRow has list[str]
  - idempotent re-run still skips existing rows (regression guard)
  - `default` key still accepted (extra="forbid" not broken)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from factory.agent_cmd.bots.init import _BotSeedEntry, _merge_bots

# ── _BotSeedEntry.model_validate coercion ────────────────────────────────────


class TestBotSeedEntryIntCoercion:
    """_BotSeedEntry.model_validate coerces int user IDs to str."""

    def test_owner_users_int_only(self) -> None:
        """Single int in owner_users is coerced to str.

        Verified: removing the @field_validator coerce method causes this to
        fail — the model would store [7377831990] (int), not ['7377831990'] (str).
        """
        # Arrange / Act
        entry = _BotSeedEntry.model_validate(
            {"bot_id": "main", "owner_users": [7377831990]}
        )

        # Assert
        assert entry.owner_users == ["7377831990"], (
            f"Expected ['7377831990'] (str-coerced); got {entry.owner_users!r}"
        )

    def test_owner_users_mixed_int_str(self) -> None:
        """Mixed list [123, '456'] → ['123', '456'] (all str).

        Verified: the validator iterates all elements; if it only coerced ints
        and left strs untouched it would still pass here — the assertion pins
        the output shape regardless of element type.
        """
        # Arrange / Act
        entry = _BotSeedEntry.model_validate(
            {"bot_id": "main", "owner_users": [123, "456"]}
        )

        # Assert
        assert entry.owner_users == ["123", "456"], (
            f"Expected ['123', '456']; got {entry.owner_users!r}"
        )

    def test_trusted_users_int_only(self) -> None:
        """Single int in trusted_users is coerced to str."""
        entry = _BotSeedEntry.model_validate(
            {"bot_id": "main", "trusted_users": [9876543210]}
        )
        assert entry.trusted_users == ["9876543210"], (
            f"Expected ['9876543210']; got {entry.trusted_users!r}"
        )

    def test_trusted_users_mixed_int_str(self) -> None:
        """trusted_users coercion mirrors owner_users: mixed list → all str."""
        entry = _BotSeedEntry.model_validate(
            {"bot_id": "main", "trusted_users": [111, "222", 333]}
        )
        assert entry.trusted_users == ["111", "222", "333"], (
            f"Expected ['111', '222', '333']; got {entry.trusted_users!r}"
        )

    def test_str_only_list_unchanged(self) -> None:
        """Already-str lists pass through without mutation."""
        entry = _BotSeedEntry.model_validate(
            {"bot_id": "main", "owner_users": ["alice", "bob"]}
        )
        assert entry.owner_users == ["alice", "bob"]

    def test_empty_list_is_valid(self) -> None:
        """Empty owner_users/trusted_users are accepted and remain empty."""
        entry = _BotSeedEntry.model_validate(
            {"bot_id": "main", "owner_users": [], "trusted_users": []}
        )
        assert entry.owner_users == []
        assert entry.trusted_users == []

    def test_default_key_accepted_extra_forbid_not_broken(self) -> None:
        """`default` key must be accepted by _BotSeedEntry (extra='forbid' allows it).

        If the `default: str | None = None` field were removed from the model
        the ValidationError would propagate and this test would fail.

        Verified: removing the `default` field from _BotSeedEntry causes
        ValidationError (extra='forbid' rejects it → test fails).
        """
        # Arrange — `default` is a real config.toml key in [[auth.telegram_bots]]
        entry = _BotSeedEntry.model_validate({"bot_id": "main", "default": "trusted"})
        # Assert — accepted without error, value stored in .default field
        assert entry.default == "trusted"


# ── _merge_bots int ID coercion ───────────────────────────────────────────────


class TestMergeBotsIntCoercion:
    """_merge_bots coerces int IDs in owner_users/trusted_users before seeding."""

    def test_merge_bots_int_owner_users(self) -> None:
        """Int IDs in telegram.bots owner_users → seeded BotRow has list[str].

        Verified: removing the `str(el) for el in v` coerce line in _merge_bots
        would leave ints in the merged dict, causing BotRow to carry int values.
        """
        # Arrange
        raw: dict = {
            "telegram": {
                "bots": [
                    {
                        "bot_id": "main",
                        "owner_users": [7377831990],
                    }
                ]
            }
        }

        # Act
        rows, errors = _merge_bots(raw)

        # Assert
        assert errors == 0, f"Expected 0 errors; got {errors}"
        assert len(rows) == 1
        assert rows[0].owner_users == ["7377831990"], (
            f"Expected ['7377831990'] (str); got {rows[0].owner_users!r}"
        )

    def test_merge_bots_mixed_owner_users_across_sections(self) -> None:
        """Int and str IDs merged across telegram.bots + auth.telegram_bots → all str.

        This exercises both the model_validate coercion (in _add_entries) and
        the deduplication logic in _merge_bots.
        """
        # Arrange — telegram.bots has an int, auth.telegram_bots has a str
        raw: dict = {
            "telegram": {
                "bots": [
                    {
                        "bot_id": "main",
                        "owner_users": [123],
                    }
                ]
            },
            "auth": {
                "telegram_bots": [
                    {
                        "bot_id": "main",
                        "owner_users": ["456"],
                    }
                ]
            },
        }

        # Act
        rows, errors = _merge_bots(raw)

        # Assert
        assert errors == 0
        assert len(rows) == 1
        # Both IDs present, both as str, order is concat order (dedup preserves first)
        owner_users = rows[0].owner_users
        assert set(owner_users) == {"123", "456"}, (
            f"Expected {{'123', '456'}}; got {owner_users!r}"
        )
        # All elements must be str (not int)
        for uid in owner_users:
            assert isinstance(uid, str), (
                f"owner_users element {uid!r} must be str; got {type(uid).__name__}"
            )

    def test_merge_bots_int_trusted_users(self) -> None:
        """Int IDs in trusted_users → BotRow carries list[str]."""
        raw: dict = {
            "telegram": {
                "bots": [
                    {
                        "bot_id": "main",
                        "trusted_users": [111222333],
                    }
                ]
            }
        }

        rows, errors = _merge_bots(raw)

        assert errors == 0
        assert len(rows) == 1
        assert rows[0].trusted_users == ["111222333"], (
            f"Expected ['111222333']; got {rows[0].trusted_users!r}"
        )

    def test_merge_bots_idempotent_does_not_duplicate_on_dedup(self) -> None:
        """Same int ID appearing twice in a section is deduplicated (str form).

        _merge_bots deduplicates via dict.fromkeys; repeating an int that
        coerces to the same str must produce exactly one entry.
        """
        raw: dict = {
            "telegram": {
                "bots": [
                    {
                        "bot_id": "main",
                        "owner_users": [999, 999],
                    }
                ]
            }
        }

        rows, errors = _merge_bots(raw)

        assert errors == 0
        assert rows[0].owner_users == ["999"], (
            f"Duplicate int IDs must dedup to single str; got {rows[0].owner_users!r}"
        )


# ── Idempotent re-run with int IDs via CLI store ──────────────────────────────


class TestBotInitIdempotentWithIntIds:
    """_merge_bots + BotStore: idempotent re-run skips existing rows."""

    def test_idempotent_re_run_skips_row(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Running bot init twice with int IDs must skip on second run.

        This exercises the full _merge_bots → BotRow → store.get(...) path.
        Verified: if _merge_bots returned different types on each call (int vs str),
        the key lookup in store.get() would miss and the row would be re-seeded.
        """

        from typer.testing import CliRunner

        from factory.cli import factory_app as app
        from tests.helpers.bot_cli import write_bot_toml
        from tests.helpers.bot_store import db_get

        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        # config.toml with int user IDs (raw TOML integers)
        write_bot_toml(
            tmp_path,
            '[[telegram.bots]]\nbot_id="main"\nagent="a"\nowner_users=[7377831990]\n',
        )

        runner = CliRunner()

        # First run: seeds the row
        result1 = runner.invoke(app, ["bot", "init"])
        assert result1.exit_code == 0, result1.output
        assert "seeded" in result1.output

        # Verify DB has str IDs
        db_path = tmp_path / "config.db"
        row = db_get(db_path, "telegram", "main")
        assert row is not None
        assert row.owner_users == ["7377831990"], (
            f"First run must store str IDs; got {row.owner_users!r}"
        )

        # Second run: must skip (idempotent)
        result2 = runner.invoke(app, ["bot", "init"])
        assert result2.exit_code == 0, result2.output
        assert "skipped" in result2.output
        assert "1 skipped" in result2.output
        assert "0 seeded" in result2.output
