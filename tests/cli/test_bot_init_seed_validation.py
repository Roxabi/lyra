"""Tests for G18 trap: unknown/typo keys in bot seed entries (issue #1420)."""

from __future__ import annotations

import pytest

from factory.agent_cmd.bots.init import _merge_bots


class TestMergeBotsUnknownKey:
    """_merge_bots seed validation: unknown keys must be rejected (G18)."""

    def test_unknown_key_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        raw = {"telegram": {"bots": [{"bot_id": "lyra", "webhook_enabel": True}]}}

        rows, errors = _merge_bots(raw)

        assert errors == 1
        assert rows == []
        captured = capsys.readouterr()
        assert "webhook_enabel" in captured.err

    def test_valid_entry_still_seeds(self) -> None:
        raw = {
            "telegram": {
                "bots": [{"bot_id": "lyra", "agent": "x", "webhook_enabled": True}]
            }
        }

        rows, errors = _merge_bots(raw)

        assert errors == 0
        assert len(rows) == 1
        assert rows[0].bot_id == "lyra"
        assert rows[0].webhook_enabled is True

    def test_mixed_batch_skips_only_bad_entry(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        raw = {
            "telegram": {
                "bots": [
                    {"bot_id": "good", "agent": "a"},
                    {"bot_id": "bad", "thread_hot_hourss": 5},
                ]
            }
        }

        rows, errors = _merge_bots(raw)

        assert errors == 1
        assert len(rows) == 1
        assert rows[0].bot_id == "good"
        captured = capsys.readouterr()
        assert "thread_hot_hourss" in captured.err

    def test_auth_keys_in_bot_section_rejected(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        raw = {
            "telegram": {
                "bots": [
                    {"bot_id": "lyra", "owner_users": ["tg:user:1"]},
                ]
            }
        }

        rows, errors = _merge_bots(raw)

        assert errors == 1
        assert rows == []
        captured = capsys.readouterr()
        assert "owner_users" in captured.err

    def test_real_config_keys_accepted(self) -> None:
        raw = {
            "telegram": {
                "bots": [
                    {
                        "bot_id": "lyra",
                        "token": "env:TELEGRAM_TOKEN",
                        "webhook_secret": "env:WH",
                        "agent": "lyra_default",
                    }
                ]
            }
        }

        rows, errors = _merge_bots(raw)

        assert errors == 0
        assert len(rows) == 1
        assert rows[0].bot_id == "lyra"
        assert rows[0].platform == "telegram"
        assert rows[0].agent == "lyra_default"