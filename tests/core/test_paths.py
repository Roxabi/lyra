"""Tests for factory.core.paths — canonical path constants (issue #977)."""

from __future__ import annotations

from factory.core.paths import PLUGINS_DIR


class TestPluginsDir:
    """PLUGINS_DIR path constant."""

    def test_plugins_dir_name(self) -> None:
        assert PLUGINS_DIR.name == "commands"
