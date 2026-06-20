"""Bundled messages.toml path resolution (#config_loader)."""

from __future__ import annotations

from pathlib import Path

import pytest

from factory.bootstrap.factory.config.config_loader import _load_messages


def test_load_messages_resolves_bundled_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FACTORY_MESSAGES_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)

    mm = _load_messages()
    # Key from src/factory/data/messages.toml (not hardcoded _FALLBACKS).
    text = mm.get("rate_limit")
    assert "usage limit" in text.lower()
