"""Tests for the shared command registry (#291).

Covers:
  - PlatformCommand / CommandParam dataclasses
  - collect_commands() merging, deduplication, sorting, admin_only flag
  - CommandRouter.command_metadata() integration
  - PluginLoader.get_command_descriptions() integration
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from lyra.core.commands.command_loader import CommandLoader
from lyra.core.commands.command_registry import (
    CommandParam,
    PlatformCommand,
    collect_commands,
)
from lyra.core.commands.command_router import CommandRouter

# ---------------------------------------------------------------------------
# collect_commands() unit tests
# ---------------------------------------------------------------------------


class TestCollectCommands:
    def test_builtins_included(self) -> None:
        meta = [("/help", "List commands", False), ("/stop", "Stop", False)]
        result = collect_commands(meta, {}, [])
        assert len(result) == 2
        assert result[0].name == "/help"
        assert result[1].name == "/stop"

    def test_admin_flag_preserved(self) -> None:
        meta = [("/config", "Config (admin-only)", True)]
        result = collect_commands(meta, {}, [])
        assert result[0].admin_only is True

    def test_plugin_commands_included(self) -> None:
        meta = [("/help", "Help", False)]
        plugins = {"/echo": "Echo back"}
        result = collect_commands(meta, plugins, [])
        names = [c.name for c in result]
        assert "/echo" in names
        assert "/help" in names

    def test_voice_commands_included(self) -> None:
        voice = [
            PlatformCommand(
                name="/join",
                description="Join voice",
                params=[CommandParam(name="mode", choices=["transient", "stay"])],
            ),
            PlatformCommand(name="/leave", description="Leave voice"),
        ]
        result = collect_commands([], {}, voice)
        assert len(result) == 2
        assert result[0].name == "/join"
        assert result[0].params[0].choices == ["transient", "stay"]

    def test_deduplication_builtin_wins(self) -> None:
        meta = [("/join", "Builtin join", False)]
        voice = [PlatformCommand(name="/join", description="Voice join")]
        result = collect_commands(meta, {}, voice)
        assert len(result) == 1
        assert result[0].description == "Builtin join"

    def test_deduplication_plugin_over_voice(self) -> None:
        plugins = {"/join": "Plugin join"}
        voice = [PlatformCommand(name="/join", description="Voice join")]
        result = collect_commands([], plugins, voice)
        assert len(result) == 1
        assert result[0].description == "Plugin join"

    def test_sorted_output(self) -> None:
        meta = [("/stop", "Stop", False), ("/help", "Help", False)]
        plugins = {"/echo": "Echo"}
        voice = [PlatformCommand(name="/aaa", description="First")]
        result = collect_commands(meta, plugins, voice)
        names = [c.name for c in result]
        assert names == sorted(names)

    def test_empty_inputs(self) -> None:
        result = collect_commands([], {}, [])
        assert result == []


# ---------------------------------------------------------------------------
# CommandRouter.command_metadata() integration
# ---------------------------------------------------------------------------


class TestCommandMetadata:
    @pytest.fixture()
    def router(self, tmp_path: Path) -> CommandRouter:
        loader = CommandLoader(tmp_path / "plugins")
        return CommandRouter(
            command_loader=loader,
            enabled_plugins=[],
        )

    def test_returns_all_builtins(self, router: CommandRouter) -> None:
        meta = router.command_metadata()
        names = [m[0] for m in meta]
        assert "/help" in names
        assert "/circuit" in names
        assert "/workspace" in names

    def test_admin_only_detected(self, router: CommandRouter) -> None:
        meta = router.command_metadata()
        admin_names = [m[0] for m in meta if m[2]]
        assert "/circuit" in admin_names
        assert "/routing" in admin_names
        assert "/config" in admin_names
        non_admin = [m[0] for m in meta if not m[2]]
        assert "/help" in non_admin
        assert "/stop" in non_admin

    def test_with_plugin_commands(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "plugins" / "echo"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.toml").write_text(
            textwrap.dedent("""\
                name = "echo"
                [[commands]]
                name = "echo"
                description = "Echo test"
                handler = "cmd_echo"
            """)
        )
        (plugin_dir / "handlers.py").write_text(
            textwrap.dedent("""\
                async def cmd_echo(msg, pool, args):
                    from lyra.core.messaging.message import Response
                    return Response(content=" ".join(args))
            """)
        )
        loader = CommandLoader(tmp_path / "plugins")
        loader.discover()
        loader.load("echo")
        router = CommandRouter(command_loader=loader, enabled_plugins=["echo"])
        meta = router.command_metadata()
        names = [m[0] for m in meta]
        assert "/echo" in names


# ---------------------------------------------------------------------------
# PluginLoader.get_command_descriptions()
# ---------------------------------------------------------------------------


class TestGetCommandDescriptions:
    def test_returns_descriptions(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "plugins" / "test_plugin"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.toml").write_text(
            textwrap.dedent("""\
                name = "test_plugin"
                [[commands]]
                name = "foo"
                description = "Foo command"
                handler = "cmd_foo"
            """)
        )
        (plugin_dir / "handlers.py").write_text(
            textwrap.dedent("""\
                async def cmd_foo(msg, pool, args):
                    from lyra.core.messaging.message import Response
                    return Response(content="foo")
            """)
        )
        loader = CommandLoader(tmp_path / "plugins")
        loader.discover()
        loader.load("test_plugin")
        result = loader.get_command_descriptions(["test_plugin"])
        assert result == {"/foo": "Foo command"}

    def test_empty_when_not_enabled(self, tmp_path: Path) -> None:
        loader = CommandLoader(tmp_path / "plugins")
        result = loader.get_command_descriptions([])
        assert result == {}
