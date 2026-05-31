"""Plugin factory helpers for tests."""

from __future__ import annotations

from pathlib import Path

from lyra.core.commands.command_loader import CommandLoader
from lyra.core.commands.command_router import CommandRouter, CommandRouterDeps
from lyra.core.config import RouterConfig

__all__ = [
    "make_echo_plugin_dir",
    "make_plugin",
    "make_router",
]


def make_plugin(
    tmp_path: Path,
    name: str,
    handler_name: str = "cmd_fn",
    cmd_name: str = "cmd",
) -> Path:
    """Create a minimal valid plugin directory under tmp_path/name/.

    Writes:
    - plugin.toml  — minimal manifest referencing *handler_name* for *cmd_name*
    - handlers.py  — async function *handler_name* that returns 'ok'
    """
    plugin_dir = tmp_path / name
    plugin_dir.mkdir(exist_ok=True)
    (plugin_dir / "plugin.toml").write_text(
        f'name = "{name}"\n'
        f"[[commands]]\n"
        f'name = "{cmd_name}"\n'
        f'description = "test"\n'
        f'handler = "{handler_name}"\n'
    )
    (plugin_dir / "handlers.py").write_text(
        f"async def {handler_name}(msg, pool, args): return 'ok'\n"
    )
    return plugin_dir


def make_echo_plugin_dir(tmpdir: Path) -> Path:
    """Create a minimal echo plugin in tmpdir/echo/."""
    plugin_dir = tmpdir / "echo"
    plugin_dir.mkdir(exist_ok=True)
    (plugin_dir / "plugin.toml").write_text(
        'name = "echo"\n'
        'description = "Echo back"\n'
        "[[commands]]\n"
        'name = "echo"\n'
        'description = "Echo back the message (test command)"\n'
        'handler = "cmd_echo"\n'
    )
    (plugin_dir / "handlers.py").write_text(
        "from lyra.core.messaging.message import Response, InboundMessage\n"
        "from lyra.core.pool import Pool\n"
        "async def cmd_echo(\n"
        "    msg: InboundMessage, pool: Pool, args: list[str]\n"
        ") -> Response:\n"
        '    return Response(content=" ".join(args))\n'
    )
    return tmpdir


def make_router(
    tmp_path: Path,
    enabled: list[str] | None = None,
    patterns: dict | None = None,
) -> CommandRouter:
    """Build a CommandRouter with the echo plugin loaded."""
    plugins_dir = make_echo_plugin_dir(tmp_path)
    loader = CommandLoader(plugins_dir)
    loader.load("echo")
    effective = enabled if enabled is not None else ["echo"]
    _patterns = patterns if patterns is not None else {"bare_url": True}
    router_config = RouterConfig(patterns=_patterns)
    return CommandRouter(
        CommandRouterDeps(
            command_loader=loader, enabled_plugins=effective, config=router_config
        )
    )
