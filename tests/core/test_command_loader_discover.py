"""Tests for CommandLoader discovery and loading (issue #106, renamed #345).

Covers:
  TestDiscover  — discovery of plugin manifests from the filesystem
  TestLoad      — loading handlers from discovered plugins
"""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

import pytest

from lyra.core.command_loader import CommandLoader, LoadedPlugin, PluginManifest

from .conftest import make_plugin

# ---------------------------------------------------------------------------
# TestDiscover
# ---------------------------------------------------------------------------


class TestDiscover:
    """discover() walks plugins_dir and returns PluginManifest objects."""

    def test_discover_finds_valid_plugin(self, tmp_path: Path) -> None:
        # Arrange
        make_plugin(tmp_path, "myplugin")
        loader = CommandLoader(plugins_dir=tmp_path)

        # Act
        manifests = loader.discover()

        # Assert
        assert len(manifests) == 1
        assert manifests[0].name == "myplugin"

    def test_discover_skips_dir_without_toml(self, tmp_path: Path) -> None:
        # Arrange — a subdirectory with no plugin.toml
        (tmp_path / "notaplugin").mkdir()
        loader = CommandLoader(plugins_dir=tmp_path)

        # Act
        manifests = loader.discover()

        # Assert — no manifests returned; directory without toml is silently ignored
        assert manifests == []

    def test_discover_skips_malformed_toml(self, tmp_path: Path) -> None:
        # Arrange — a directory whose plugin.toml is not valid TOML
        plugin_dir = tmp_path / "broken"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.toml").write_text("name = [unclosed bracket\n")
        loader = CommandLoader(plugins_dir=tmp_path)

        # Act + Assert — no exception raised; broken plugin silently skipped
        manifests = loader.discover()
        assert manifests == []

    def test_discover_skips_missing_name_field(self, tmp_path: Path) -> None:
        # Arrange — valid TOML but missing the required 'name' key
        plugin_dir = tmp_path / "noname"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.toml").write_text(
            'description = "no name"\n'
            "[[commands]]\n"
            'name = "cmd"\n'
            'description = "test"\n'
            'handler = "fn"\n'
        )
        loader = CommandLoader(plugins_dir=tmp_path)

        # Act + Assert — no exception raised; plugin without 'name' silently skipped
        manifests = loader.discover()
        assert manifests == []

    def test_discover_returns_manifest_fields(self, tmp_path: Path) -> None:
        # Arrange — a plugin with explicit optional fields set
        plugin_dir = tmp_path / "richplugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.toml").write_text(
            'name = "richplugin"\n'
            'description = "A rich plugin"\n'
            'version = "2.0.0"\n'
            "priority = 5\n"
            "enabled = false\n"
            "timeout = 10.0\n"
            "[[commands]]\n"
            'name = "ping"\n'
            'description = "Ping command"\n'
            'handler = "do_ping"\n'
        )
        (plugin_dir / "handlers.py").write_text(
            "async def do_ping(msg, pool, args): return 'pong'\n"
        )
        loader = CommandLoader(plugins_dir=tmp_path)

        # Act
        manifests = loader.discover()

        # Assert
        assert len(manifests) == 1
        m = manifests[0]
        assert m.name == "richplugin"
        assert m.description == "A rich plugin"
        assert m.version == "2.0.0"
        assert m.priority == 5
        assert m.enabled is False
        assert m.timeout == 10.0
        assert len(m.commands) == 1
        assert m.commands[0].name == "ping"
        assert m.commands[0].description == "Ping command"
        assert m.commands[0].handler == "do_ping"

    def test_discover_returns_list_of_plugin_manifest(self, tmp_path: Path) -> None:
        # Arrange
        make_plugin(tmp_path, "alpha")
        make_plugin(tmp_path, "beta")
        loader = CommandLoader(plugins_dir=tmp_path)

        # Act
        manifests = loader.discover()

        # Assert — both manifests found; return type is list of PluginManifest
        assert len(manifests) == 2
        assert all(isinstance(m, PluginManifest) for m in manifests)
        names = {m.name for m in manifests}
        assert names == {"alpha", "beta"}

    def test_discover_empty_plugins_dir(self, tmp_path: Path) -> None:
        # Arrange — empty directory
        loader = CommandLoader(plugins_dir=tmp_path)

        # Act
        manifests = loader.discover()

        # Assert
        assert manifests == []


# ---------------------------------------------------------------------------
# TestLoad
# ---------------------------------------------------------------------------


class TestLoad:
    """load() imports handlers.py and builds a LoadedPlugin."""

    def test_load_resolves_handler(self, tmp_path: Path) -> None:
        # Arrange
        make_plugin(tmp_path, "echoplugin", handler_name="do_echo", cmd_name="echo")
        loader = CommandLoader(plugins_dir=tmp_path)

        # Act
        loaded = loader.load("echoplugin")

        # Assert — "/echo" key maps to a callable
        assert "/echo" in loaded.handlers
        assert callable(loaded.handlers["/echo"])

    def test_load_raises_for_missing_handler(self, tmp_path: Path) -> None:
        # Arrange — manifest references "nonexistent_fn" but handlers.py omits it
        plugin_dir = tmp_path / "badplugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.toml").write_text(
            'name = "badplugin"\n'
            "[[commands]]\n"
            'name = "cmd"\n'
            'description = "test"\n'
            'handler = "nonexistent_fn"\n'
        )
        (plugin_dir / "handlers.py").write_text(
            "async def some_other_fn(msg, pool, args): return 'ok'\n"
        )
        loader = CommandLoader(plugins_dir=tmp_path)

        # Act + Assert
        with pytest.raises(ValueError, match="not found or not callable"):
            loader.load("badplugin")

    def test_load_raises_for_noncallable_handler(self, tmp_path: Path) -> None:
        # Arrange — manifest references "MY_CONSTANT" which is a string, not a function
        plugin_dir = tmp_path / "strplugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.toml").write_text(
            'name = "strplugin"\n'
            "[[commands]]\n"
            'name = "cmd"\n'
            'description = "test"\n'
            'handler = "MY_CONSTANT"\n'
        )
        (plugin_dir / "handlers.py").write_text('MY_CONSTANT = "not a function"\n')
        loader = CommandLoader(plugins_dir=tmp_path)

        # Act + Assert
        with pytest.raises(ValueError, match="not found or not callable"):
            loader.load("strplugin")

    def test_load_populates_loaded_dict(self, tmp_path: Path) -> None:
        # Arrange
        make_plugin(tmp_path, "myplugin")
        loader = CommandLoader(plugins_dir=tmp_path)

        # Act
        loader.load("myplugin")

        # Assert — internal cache is populated
        assert "myplugin" in loader._loaded
        assert isinstance(loader._loaded["myplugin"], LoadedPlugin)

    def test_load_returns_loaded_plugin_with_correct_name(self, tmp_path: Path) -> None:
        # Arrange
        make_plugin(tmp_path, "testplugin")
        loader = CommandLoader(plugins_dir=tmp_path)

        # Act
        loaded = loader.load("testplugin")

        # Assert
        assert isinstance(loaded, LoadedPlugin)
        assert loaded.name == "testplugin"
        assert isinstance(loaded.manifest, PluginManifest)
        assert isinstance(loaded.module, ModuleType)

    def test_load_handlers_dict_keyed_with_slash_prefix(self, tmp_path: Path) -> None:
        # Arrange
        make_plugin(tmp_path, "slashplugin", handler_name="run_cmd", cmd_name="run")
        loader = CommandLoader(plugins_dir=tmp_path)

        # Act
        loaded = loader.load("slashplugin")

        # Assert — command name "run" becomes key "/run"
        assert "/run" in loaded.handlers
        assert "run" not in loaded.handlers

    def test_load_rejects_symlinked_handlers_outside_plugins_dir(
        self, tmp_path: Path
    ) -> None:
        # Arrange — handlers.py is a symlink to a file outside plugins_dir
        evil_dir = tmp_path / "outside"
        evil_dir.mkdir()
        (evil_dir / "handlers.py").write_text(
            "async def cmd_fn(msg, pool, args): return 'pwned'\n"
        )
        plugin_dir = tmp_path / "plugins" / "legit"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.toml").write_text(
            'name = "legit"\n'
            "[[commands]]\n"
            'name = "cmd"\n'
            'description = "test"\n'
            'handler = "cmd_fn"\n'
        )
        (plugin_dir / "handlers.py").symlink_to(evil_dir / "handlers.py")
        loader = CommandLoader(plugins_dir=tmp_path / "plugins")

        # Act + Assert — symlink escape is detected and rejected
        with pytest.raises(ValueError, match="resolves outside plugins directory"):
            loader.load("legit")

    def test_load_rejects_symlinked_plugin_dir_outside_plugins_dir(
        self, tmp_path: Path
    ) -> None:
        # Arrange — plugin directory itself is a symlink to outside plugins_dir
        outside = tmp_path / "outside" / "evil"
        outside.mkdir(parents=True)
        (outside / "plugin.toml").write_text(
            'name = "evil"\n'
            "[[commands]]\n"
            'name = "cmd"\n'
            'description = "test"\n'
            'handler = "cmd_fn"\n'
        )
        (outside / "handlers.py").write_text(
            "async def cmd_fn(msg, pool, args): return 'pwned'\n"
        )
        plugins = tmp_path / "plugins"
        plugins.mkdir()
        (plugins / "evil").symlink_to(outside)
        loader = CommandLoader(plugins_dir=plugins)

        # Act + Assert — symlinked directory escapes plugins_dir
        with pytest.raises(ValueError, match="escapes plugins directory"):
            loader.load("evil")

    def test_load_rejects_symlinked_plugin_toml_outside_plugins_dir(
        self, tmp_path: Path
    ) -> None:
        # Arrange — plugin.toml is a symlink to a file outside plugins_dir
        evil_dir = tmp_path / "outside"
        evil_dir.mkdir()
        (evil_dir / "evil.toml").write_text(
            'name = "legit"\n'
            "[[commands]]\n"
            'name = "cmd"\n'
            'description = "test"\n'
            'handler = "cmd_fn"\n'
        )
        plugin_dir = tmp_path / "plugins" / "legit"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.toml").symlink_to(evil_dir / "evil.toml")
        (plugin_dir / "handlers.py").write_text(
            "async def cmd_fn(msg, pool, args): return 'ok'\n"
        )
        loader = CommandLoader(plugins_dir=tmp_path / "plugins")

        # Act + Assert — symlinked plugin.toml is detected and rejected
        with pytest.raises(ValueError, match="resolves outside plugins directory"):
            loader.load("legit")

    def test_load_rejects_nested_symlinked_handlers(self, tmp_path: Path) -> None:
        # Arrange — two-hop symlink chain: handlers.py -> link -> outside file
        evil_dir = tmp_path / "outside"
        evil_dir.mkdir()
        (evil_dir / "real.py").write_text(
            "async def cmd_fn(msg, pool, args): return 'pwned'\n"
        )
        intermediate = tmp_path / "intermediate_link"
        intermediate.symlink_to(evil_dir / "real.py")

        plugins = tmp_path / "plugins"
        plugin_dir = plugins / "legit"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.toml").write_text(
            'name = "legit"\n'
            "[[commands]]\n"
            'name = "cmd"\n'
            'description = "test"\n'
            'handler = "cmd_fn"\n'
        )
        (plugin_dir / "handlers.py").symlink_to(intermediate)
        loader = CommandLoader(plugins_dir=plugins)

        # Act + Assert — nested symlink chain is fully resolved and rejected
        with pytest.raises(ValueError, match="resolves outside plugins directory"):
            loader.load("legit")

    def test_load_circular_symlink_raises_safely(self, tmp_path: Path) -> None:
        # Arrange — self-referential symlink
        plugin_dir = tmp_path / "circular"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.toml").write_text(
            'name = "circular"\n'
            "[[commands]]\n"
            'name = "cmd"\n'
            'description = "test"\n'
            'handler = "cmd_fn"\n'
        )
        handlers = plugin_dir / "handlers.py"
        handlers.symlink_to(handlers)  # circular
        loader = CommandLoader(plugins_dir=tmp_path)

        # Act + Assert — circular symlink raises RuntimeError (ELOOP) from .resolve()
        with pytest.raises((OSError, RuntimeError)):
            loader.load("circular")

    def test_load_rejects_manifest_name_mismatch(self, tmp_path: Path) -> None:
        # Arrange — directory "legit" but manifest says name = "evil"
        plugin_dir = tmp_path / "legit"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.toml").write_text(
            'name = "evil"\n'
            "[[commands]]\n"
            'name = "cmd"\n'
            'description = "test"\n'
            'handler = "cmd_fn"\n'
        )
        (plugin_dir / "handlers.py").write_text(
            "async def cmd_fn(msg, pool, args): return 'ok'\n"
        )
        loader = CommandLoader(plugins_dir=tmp_path)

        # Act + Assert — mismatched name is detected
        with pytest.raises(ValueError, match="mismatched name"):
            loader.load("legit")

    def test_load_raises_for_unknown_plugin_name(self, tmp_path: Path) -> None:
        # Arrange — no plugin named "ghost" exists
        loader = CommandLoader(plugins_dir=tmp_path)

        # Act + Assert
        with pytest.raises((ValueError, FileNotFoundError, KeyError)):
            loader.load("ghost")
