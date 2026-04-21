"""Tests for lyra.core.agent.agent_commands — CommandReloadManager hash verification."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

from lyra.core.agent.agent_commands import CommandReloadManager, _file_sha256

# ---------------------------------------------------------------------------
# _file_sha256
# ---------------------------------------------------------------------------


class TestFileSha256:
    def test_returns_hex_digest_for_valid_file(self, tmp_path: Path) -> None:
        p = tmp_path / "test.py"
        p.write_text("print('hello')")
        result = _file_sha256(p)
        assert len(result) == 64  # SHA-256 hex digest length
        assert all(c in "0123456789abcdef" for c in result)

    def test_returns_empty_string_for_missing_file(self, tmp_path: Path) -> None:
        p = tmp_path / "nonexistent.py"
        assert _file_sha256(p) == ""

    def test_different_content_produces_different_hash(self, tmp_path: Path) -> None:
        p = tmp_path / "test.py"
        p.write_text("version 1")
        h1 = _file_sha256(p)
        p.write_text("version 2")
        h2 = _file_sha256(p)
        assert h1 != h2


# ---------------------------------------------------------------------------
# CommandReloadManager — forged mtime attack (M-11)
# ---------------------------------------------------------------------------


def _make_plugin(plugins_dir: Path, name: str, code: str) -> Path:
    """Create a minimal plugin directory with handlers.py and plugin.toml."""
    plugin_dir = plugins_dir / name
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text(
        f'name = "{name}"\n'
        f'description = "test"\n'
        "[[commands]]\n"
        f'name = "{name}"\n'
        'description = "test"\n'
        'handler = "cmd_handler"\n'
    )
    handlers = plugin_dir / "handlers.py"
    handlers.write_text(code)
    return handlers


class TestCommandReloadHashVerification:
    """M-11: forged mtime should NOT trigger reload; content change should."""

    def test_forged_mtime_does_not_reload(self, tmp_path: Path) -> None:
        """Mtime advances but content is identical → reload skipped."""
        plugins_dir = tmp_path / "plugins"
        handlers = _make_plugin(
            plugins_dir,
            "echo",
            "async def cmd_handler(msg, pool, args): pass\n",
        )

        loader = MagicMock()
        loader.discover.return_value = []
        loader.load.return_value = None
        config = MagicMock()
        config.commands_enabled = ["echo"]

        mgr = CommandReloadManager(config, loader, plugins_dir)
        loader.reload.reset_mock()

        # Advance mtime without changing content
        old_mtime = handlers.stat().st_mtime
        os.utime(handlers, (old_mtime + 10, old_mtime + 10))

        result = mgr.reload_plugins()

        assert result is False
        loader.reload.assert_not_called()
        # mtime should still be updated to avoid re-checking
        assert mgr.command_mtimes["echo"] == old_mtime + 10

    def test_content_change_triggers_reload(self, tmp_path: Path) -> None:
        """Mtime advances AND content changes → reload triggered."""
        plugins_dir = tmp_path / "plugins"
        handlers = _make_plugin(
            plugins_dir,
            "echo",
            "async def cmd_handler(msg, pool, args): pass\n",
        )

        loader = MagicMock()
        loader.discover.return_value = []
        loader.load.return_value = None
        config = MagicMock()
        config.commands_enabled = ["echo"]

        mgr = CommandReloadManager(config, loader, plugins_dir)
        loader.reload.reset_mock()

        # Change content AND advance mtime
        handlers.write_text("async def cmd_handler(msg, pool, args): return 'v2'\n")
        new_mtime = handlers.stat().st_mtime + 10
        os.utime(handlers, (new_mtime, new_mtime))

        result = mgr.reload_plugins()

        assert result is True
        loader.reload.assert_called_once_with("echo")
