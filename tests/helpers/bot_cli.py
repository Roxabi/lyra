"""CLI bot test helpers (plain functions, no pytest fixtures)."""

from __future__ import annotations

from pathlib import Path


def write_bot_toml(tmp_path: Path, text: str) -> Path:
    """Write *text* to ``tmp_path / "config.toml"`` and return the path."""
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path
