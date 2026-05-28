"""RED tests for G18 trap: unknown/typo keys in bot seed entries (issue #1420 T9).

T10 will add a Pydantic model with extra="forbid" to _merge_bots so that
unknown keys (e.g. webhook_enabel) are rejected, skip the entry, increment
validation_errors, and emit the offending key name to stderr.

Contract (pinned, T10 implements to match):
  - _merge_bots(raw) -> tuple[list[BotRow], int] = (rows, validation_errors)
  - Entry with unknown key: skipped, validation_errors += 1, stderr names key
  - Legal seed keys: bot_id, agent, webhook_enabled, default_trust,
    owner_users, trusted_users, trusted_roles, auto_thread, thread_hot_hours
"""

from __future__ import annotations

import pytest

from lyra.agent_cmd.bots.init import _merge_bots


class TestMergeBotsUnknownKey:
    """_merge_bots seed validation: unknown keys must be rejected (G18)."""

    def test_unknown_key_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        # Arrange — typo key webhook_enabel (misspelling of webhook_enabled)
        raw = {"telegram": {"bots": [{"bot_id": "lyra", "webhook_enabel": True}]}}

        # Act
        rows, errors = _merge_bots(raw)

        # Assert — entry is skipped, error counted, offending key named on stderr
        # RED: today errors == 0 and rows has one BotRow (typo silently dropped)
        assert errors == 1, (
            f"Expected 1 validation error for typo key 'webhook_enabel', got {errors}. "
            "T10 must add Pydantic seed model with extra='forbid' to catch this."
        )
        assert rows == [], (
            f"Expected entry with typo key to be skipped (rows=[]), got {rows}."
        )
        captured = capsys.readouterr()
        assert "webhook_enabel" in captured.err, (
            f"Expected offending key 'webhook_enabel' named in stderr,"
            f" got: {captured.err!r}"
        )

    def test_valid_entry_still_seeds(self) -> None:
        # Arrange — all legal keys, no unknown keys
        raw = {
            "telegram": {
                "bots": [{"bot_id": "lyra", "agent": "x", "webhook_enabled": True}]
            }
        }

        # Act
        rows, errors = _merge_bots(raw)

        # Assert — valid entry seeds normally, no errors
        assert errors == 0, f"Expected 0 errors for valid entry, got {errors}."
        assert len(rows) == 1, f"Expected 1 row, got {len(rows)}."
        assert rows[0].bot_id == "lyra"
        assert rows[0].webhook_enabled is True

    def test_mixed_batch_skips_only_bad_entry(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Arrange — two entries: one valid, one with typo key thread_hot_hours
        # (thread_hot_hours is the correct spelling; here we use a misspelling to
        # simulate a typo)
        raw = {
            "telegram": {
                "bots": [
                    {"bot_id": "good", "agent": "a"},
                    {"bot_id": "bad", "thread_hot_hourss": 5},  # typo: extra 's'
                ]
            }
        }

        # Act
        rows, errors = _merge_bots(raw)

        # Assert — only the bad entry is skipped; good entry seeds normally
        assert errors == 1, (
            f"Expected 1 validation error for typo key, got {errors}. "
            "Validator must be per-entry, not abort the whole batch."
        )
        assert len(rows) == 1, (
            f"Expected 1 row (only 'good' entry), got {len(rows)}. "
            "The valid entry must still seed when a sibling entry is invalid."
        )
        assert rows[0].bot_id == "good", (
            f"Expected rows[0].bot_id == 'good', got {rows[0].bot_id!r}."
        )
        captured = capsys.readouterr()
        assert "thread_hot_hourss" in captured.err, (
            f"Expected offending key 'thread_hot_hourss' named in stderr,"
            f" got: {captured.err!r}"
        )

    def test_auth_section_entry_also_validated(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Arrange — unknown key in [[auth.telegram_bots]] section
        raw = {
            "auth": {
                "telegram_bots": [
                    {"bot_id": "lyra", "unknwon_key": "oops"}  # typo key
                ]
            }
        }

        # Act
        rows, errors = _merge_bots(raw)

        # Assert — auth section entries go through the same validator
        # (G18 covers all 4 sections)
        assert errors == 1, (
            f"Expected 1 validation error for typo key in auth.telegram_bots,"
            f" got {errors}. "
            "All four config sections must pass through _BotSeedEntry validation."
        )
        assert rows == [], (
            f"Expected entry with typo key to be skipped (rows=[]), got {rows}."
        )
        captured = capsys.readouterr()
        assert "unknwon_key" in captured.err, (
            f"Expected offending key 'unknwon_key' named in stderr,"
            f" got: {captured.err!r}"
        )
