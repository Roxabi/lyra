"""Tests for lyra.core.paths — canonical path constants (issue #977).

RED: lyra.core.paths does not exist yet; this test is intentionally failing
until the module is created by backend-dev.
"""

from __future__ import annotations

from lyra.core.paths import PLUGINS_DIR  # type: ignore[import-untyped]


class TestPluginsDir:
    """PLUGINS_DIR path constant."""

    def test_plugins_dir_name(self) -> None:
        assert PLUGINS_DIR.name == "commands"
