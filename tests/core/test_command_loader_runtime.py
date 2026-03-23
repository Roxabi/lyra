"""Tests for CommandLoader runtime behaviour (issue #106, renamed #345).

Covers:
  TestGetCommands    — filtering and building the handler dispatch map
  TestReload         — hot-reload of a plugin's handlers module (V4)
  TestPerAgentConfig — enabled/disabled flag semantics (V3)
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from lyra.core.commands.command_loader import CommandLoader, LoadedPlugin

from .conftest import make_plugin

# ---------------------------------------------------------------------------
# TestGetCommands
# ---------------------------------------------------------------------------


class TestGetCommands:
    """get_commands() returns handler dispatch map filtered to enabled plugins."""

    def test_get_commands_returns_enabled_only(self, tmp_path: Path) -> None:
        # Arrange — two plugins: only "alpha" is in the enabled list
        make_plugin(tmp_path, "alpha", handler_name="alpha_fn", cmd_name="alpha")
        make_plugin(tmp_path, "beta", handler_name="beta_fn", cmd_name="beta")
        loader = CommandLoader(plugins_dir=tmp_path)
        loader.load("alpha")
        loader.load("beta")

        # Act
        commands = loader.get_commands(enabled=["alpha"])

        # Assert — only alpha's command key is present
        assert "/alpha" in commands
        assert "/beta" not in commands

    def test_get_commands_empty_enabled(self, tmp_path: Path) -> None:
        # Arrange
        make_plugin(tmp_path, "myplugin")
        loader = CommandLoader(plugins_dir=tmp_path)
        loader.load("myplugin")

        # Act
        commands = loader.get_commands(enabled=[])

        # Assert
        assert commands == {}

    def test_get_commands_slash_prefixed_keys(self, tmp_path: Path) -> None:
        # Arrange — command name "echo" in plugin
        make_plugin(tmp_path, "echoplugin", handler_name="do_echo", cmd_name="echo")
        loader = CommandLoader(plugins_dir=tmp_path)
        loader.load("echoplugin")

        # Act
        commands = loader.get_commands(enabled=["echoplugin"])

        # Assert — key is "/echo" not "echo"
        assert "/echo" in commands
        assert "echo" not in commands

    def test_get_commands_all_handlers_are_callable(self, tmp_path: Path) -> None:
        # Arrange
        make_plugin(tmp_path, "myplugin", handler_name="handler_fn", cmd_name="go")
        loader = CommandLoader(plugins_dir=tmp_path)
        loader.load("myplugin")

        # Act
        commands = loader.get_commands(enabled=["myplugin"])

        # Assert — all values in the dict are callable
        for cmd_key, handler in commands.items():
            assert callable(handler), f"{cmd_key} handler is not callable"

    def test_get_commands_multiple_enabled_plugins(self, tmp_path: Path) -> None:
        # Arrange — three plugins, two enabled
        make_plugin(tmp_path, "p1", handler_name="fn1", cmd_name="cmd1")
        make_plugin(tmp_path, "p2", handler_name="fn2", cmd_name="cmd2")
        make_plugin(tmp_path, "p3", handler_name="fn3", cmd_name="cmd3")
        loader = CommandLoader(plugins_dir=tmp_path)
        loader.load("p1")
        loader.load("p2")
        loader.load("p3")

        # Act
        commands = loader.get_commands(enabled=["p1", "p2"])

        # Assert
        assert "/cmd1" in commands
        assert "/cmd2" in commands
        assert "/cmd3" not in commands

    def test_get_commands_ignores_unloaded_enabled_names(self, tmp_path: Path) -> None:
        # Arrange — enabled list references a plugin that was never load()ed
        make_plugin(tmp_path, "loaded_plugin", handler_name="fn", cmd_name="run")
        loader = CommandLoader(plugins_dir=tmp_path)
        loader.load("loaded_plugin")

        # Act — "ghost" is enabled but was never loaded
        commands = loader.get_commands(enabled=["loaded_plugin", "ghost"])

        # Assert — no crash; only loaded_plugin's commands appear
        assert "/run" in commands


# ---------------------------------------------------------------------------
# TestReload (V4 tests, written in RED phase)
# ---------------------------------------------------------------------------


class TestReload:
    """reload() re-imports handlers.py and updates the loaded plugin in-place."""

    def test_reload_updates_handler(self, tmp_path: Path) -> None:
        # Arrange — create plugin with initial handler returning 'original'
        plugin_dir = tmp_path / "hotplugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.toml").write_text(
            'name = "hotplugin"\n'
            "[[commands]]\n"
            'name = "greet"\n'
            'description = "Greet"\n'
            'handler = "greet_fn"\n'
        )
        (plugin_dir / "handlers.py").write_text(
            "async def greet_fn(msg, pool, args): return 'original'\n"
        )
        loader = CommandLoader(plugins_dir=tmp_path)
        loader.load("hotplugin")

        original_handler = loader._loaded["hotplugin"].handlers["/greet"]

        # Act — modify handlers.py on disk, then reload
        (plugin_dir / "handlers.py").write_text(
            "async def greet_fn(msg, pool, args): return 'updated'\n"
        )
        loader.reload("hotplugin")

        # Assert — the handler in the loaded dict is now from the updated module
        updated_handler = loader._loaded["hotplugin"].handlers["/greet"]
        assert updated_handler is not original_handler

        # Verify by calling the new handler (cast to Any to avoid strict type check
        # on the synthetic None msg/pool used in this unit test)

        result = asyncio.get_event_loop().run_until_complete(
            updated_handler(None, None, [])  # type: ignore[arg-type]
        )
        assert result == "updated"

    def test_reload_unknown_plugin_calls_load(self, tmp_path: Path) -> None:
        # Arrange — plugin exists on disk but has never been loaded
        make_plugin(tmp_path, "freshplugin", handler_name="go_fn", cmd_name="go")
        loader = CommandLoader(plugins_dir=tmp_path)
        assert "freshplugin" not in loader._loaded

        # Act — reload on an unloaded plugin (equivalent to load)
        loaded = loader.reload("freshplugin")

        # Assert — plugin is now in the loaded dict
        assert "freshplugin" in loader._loaded
        assert isinstance(loaded, LoadedPlugin)
        assert loaded.name == "freshplugin"

    def test_reload_preserves_manifest_reference(self, tmp_path: Path) -> None:
        # Arrange
        make_plugin(
            tmp_path, "stableplugin", handler_name="stable_fn", cmd_name="stable"
        )
        loader = CommandLoader(plugins_dir=tmp_path)
        loader.load("stableplugin")
        original_manifest = loader._loaded["stableplugin"].manifest

        # Act
        loader.reload("stableplugin")

        # Assert — manifest fields remain consistent (name unchanged by reload)
        reloaded_manifest = loader._loaded["stableplugin"].manifest
        assert reloaded_manifest.name == original_manifest.name

    def test_reload_rejects_symlinked_handlers_outside_plugins_dir(
        self, tmp_path: Path
    ) -> None:
        # Arrange — load a clean plugin, then swap handlers.py to a symlink
        plugins = tmp_path / "plugins"
        plugins.mkdir()
        make_plugin(plugins, "hotplugin", handler_name="cmd_fn", cmd_name="cmd")
        loader = CommandLoader(plugins_dir=plugins)
        loader.load("hotplugin")

        evil_dir = tmp_path / "outside"
        evil_dir.mkdir()
        (evil_dir / "handlers.py").write_text(
            "async def cmd_fn(msg, pool, args): return 'pwned'\n"
        )
        handlers = plugins / "hotplugin" / "handlers.py"
        handlers.unlink()
        handlers.symlink_to(evil_dir / "handlers.py")

        # Act + Assert — reload detects the symlink escape
        with pytest.raises(ValueError, match="resolves outside plugins directory"):
            loader.reload("hotplugin")


# ---------------------------------------------------------------------------
# TestPerAgentConfig (V3 tests)
# ---------------------------------------------------------------------------


class TestPerAgentConfig:
    """enabled flag in plugin.toml signals per-agent filtering semantics."""

    def test_manifest_enabled_false_signals_skip(self, tmp_path: Path) -> None:
        # Arrange — a plugin explicitly disabled in its manifest
        plugin_dir = tmp_path / "disabledplugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.toml").write_text(
            'name = "disabledplugin"\n'
            "enabled = false\n"
            "[[commands]]\n"
            'name = "cmd"\n'
            'description = "test"\n'
            'handler = "fn"\n'
        )
        (plugin_dir / "handlers.py").write_text(
            "async def fn(msg, pool, args): return 'ok'\n"
        )
        loader = CommandLoader(plugins_dir=tmp_path)

        # Act
        manifests = loader.discover()

        # Assert — manifest is returned (discover does not filter by enabled),
        # but the enabled flag is False so AgentBase can filter it downstream
        assert len(manifests) == 1
        assert manifests[0].name == "disabledplugin"
        assert manifests[0].enabled is False

    def test_manifest_enabled_defaults_to_true(self, tmp_path: Path) -> None:
        # Arrange — a plugin that omits the 'enabled' key entirely
        plugin_dir = tmp_path / "defaultplugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.toml").write_text(
            'name = "defaultplugin"\n'
            "[[commands]]\n"
            'name = "cmd"\n'
            'description = "test"\n'
            'handler = "fn"\n'
        )
        (plugin_dir / "handlers.py").write_text(
            "async def fn(msg, pool, args): return 'ok'\n"
        )
        loader = CommandLoader(plugins_dir=tmp_path)

        # Act
        manifests = loader.discover()

        # Assert — enabled defaults to True (default-open policy)
        assert len(manifests) == 1
        assert manifests[0].enabled is True

    def test_default_open_loads_enabled_true_manifests(self, tmp_path: Path) -> None:
        """Documents expected AgentBase behaviour: plugins with enabled=True in their
        manifest are included when building the initial command dispatch map.

        This test exercises the PluginLoader layer only (not AgentBase). It verifies
        that discover() + get_commands() together support a default-open policy:
        an agent that passes [m.name for m in manifests if m.enabled] to get_commands()
        will get all handlers for enabled plugins.
        """
        # Arrange — one enabled, one explicitly disabled
        make_plugin(tmp_path, "enabled_plugin", handler_name="e_fn", cmd_name="e_cmd")
        plugin_dir = tmp_path / "disabled_plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.toml").write_text(
            'name = "disabled_plugin"\n'
            "enabled = false\n"
            "[[commands]]\n"
            'name = "d_cmd"\n'
            'description = "disabled"\n'
            'handler = "d_fn"\n'
        )
        (plugin_dir / "handlers.py").write_text(
            "async def d_fn(msg, pool, args): return 'nope'\n"
        )
        loader = CommandLoader(plugins_dir=tmp_path)

        # Act — simulate AgentBase default-open logic
        manifests = loader.discover()
        enabled_names = [m.name for m in manifests if m.enabled]
        for name in enabled_names:
            loader.load(name)
        commands = loader.get_commands(enabled=enabled_names)

        # Assert — only the enabled plugin's command appears
        assert "/e_cmd" in commands
        assert "/d_cmd" not in commands

    def test_plugin_manifest_is_frozen(self, tmp_path: Path) -> None:
        # Arrange
        make_plugin(tmp_path, "frozenplugin")
        loader = CommandLoader(plugins_dir=tmp_path)

        # Act
        manifests = loader.discover()
        manifest = manifests[0]

        # Assert — PluginManifest is a frozen dataclass; mutation raises
        with pytest.raises(AttributeError):
            manifest.name = "tampered"  # type: ignore[misc]
