"""Tests for __main__: env var validation (T1)."""

from __future__ import annotations

import pytest


class TestMissingEnvVars:
    def test_missing_telegram_token_exits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)

        from lyra.adapters.telegram import load_config

        with pytest.raises(SystemExit, match="TELEGRAM_TOKEN"):
            load_config()

    def test_missing_telegram_secret_exits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TELEGRAM_TOKEN", "fake")
        monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)

        from lyra.adapters.telegram import load_config

        with pytest.raises(SystemExit, match="TELEGRAM_WEBHOOK_SECRET"):
            load_config()

    def test_missing_discord_token_exits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DISCORD_TOKEN", raising=False)

        from lyra.adapters.discord_config import load_discord_config

        with pytest.raises(SystemExit, match="DISCORD_TOKEN"):
            load_discord_config()
