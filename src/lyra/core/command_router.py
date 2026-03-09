"""Command router for Lyra hub (issue #66, updated #106).

Dispatches slash-prefixed messages to plugin handlers loaded by PluginLoader,
or to built-in handlers (/help, /circuit). SkillHandler and SKILL_REGISTRY removed.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from .circuit_breaker import CircuitRegistry
from .message import Message, Response, TextContent
from .plugin_loader import AsyncHandler, PluginLoader
from .pool import Pool

log = logging.getLogger(__name__)

# Matches a message that starts with "/" followed by at least one word character.
_COMMAND_RE = re.compile(r"^/\w")


@dataclass(frozen=True)
class CommandConfig:
    """Configuration for a built-in slash command."""

    description: str = ""
    builtin: bool = False
    timeout: float = 30.0


class CommandRouter:
    """Routes slash commands to plugin handlers or built-in handlers."""

    _DEFAULT_BUILTINS: dict[str, CommandConfig] = {
        "/help": CommandConfig(builtin=True, description="List available commands"),
        "/circuit": CommandConfig(
            builtin=True, description="Show circuit breaker status (admin-only)"
        ),
    }

    def __init__(
        self,
        plugin_loader: PluginLoader,
        enabled_plugins: list[str],
        builtins: dict[str, CommandConfig] | None = None,
        circuit_registry: CircuitRegistry | None = None,
        admin_user_ids: set[str] | None = None,
    ) -> None:
        self._plugin_loader = plugin_loader
        self._enabled_plugins = enabled_plugins
        self._builtins: dict[str, CommandConfig] = (
            builtins if builtins is not None else dict(self._DEFAULT_BUILTINS)
        )
        self._circuit_registry = circuit_registry
        self._admin_user_ids = admin_user_ids or set()
        # Guard: raise early if any loaded plugin command clashes with a builtin.
        plugin_handlers = plugin_loader.get_commands(enabled_plugins)
        conflicts = set(plugin_handlers) & set(self._builtins)
        if conflicts:
            raise ValueError(
                f"Plugin command(s) clash with builtins: {sorted(conflicts)}. "
                "Rename the plugin command or remove the builtin."
            )

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def is_command(self, msg: Message) -> bool:
        """Return True if the message starts with '/' followed by a word char."""
        content = msg.content
        if isinstance(content, TextContent):
            text = content.text
        elif isinstance(content, str):
            text = content
        else:
            return False
        return bool(_COMMAND_RE.match(text))

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def dispatch(self, msg: Message, pool: Pool | None = None) -> Response:
        """Parse the command name + args and route to the appropriate handler."""
        content = msg.content
        if isinstance(content, TextContent):
            text = content.text
        else:
            text = str(content)

        parts = text.split()
        command_name = parts[0].lower()
        args = parts[1:]

        if command_name == "/help":
            return self._help()

        if command_name == "/circuit":
            return self._circuit_status(msg)

        builtin = self._builtins.get(command_name)
        if builtin and builtin.builtin:
            return Response(
                content=f"Built-in command {command_name} is not yet implemented."
            )

        plugin_handlers: dict[str, AsyncHandler] = self._plugin_loader.get_commands(
            self._enabled_plugins
        )
        handler = plugin_handlers.get(command_name)
        if handler is None:
            return Response(
                content=f"Unknown command: {command_name}. "
                "Type /help for available commands."
            )

        if pool is None:
            raise TypeError(
                f"Plugin command '{command_name}' requires a real Pool instance. "
                "Pass pool=<Pool> when calling dispatch() for plugin commands."
            )
        return await handler(msg, pool, args)

    # ------------------------------------------------------------------
    # Builtins
    # ------------------------------------------------------------------

    def _help(self) -> Response:
        """Return a listing of all available commands (builtins + plugins)."""
        lines: list[str] = ["Available commands:"]
        # Builtins
        for cmd_name, cfg in sorted(self._builtins.items()):
            desc = cfg.description or "(no description)"
            lines.append(f"  {cmd_name} — {desc}")
        # Plugin commands
        plugin_handlers = self._plugin_loader.get_commands(self._enabled_plugins)
        for cmd_name in sorted(plugin_handlers):
            if cmd_name not in self._builtins:
                lines.append(f"  {cmd_name} — (plugin command)")
        return Response(content="\n".join(lines))

    def _circuit_status(self, msg: Message) -> Response:
        """Return circuit status table (admin-only)."""
        sender_id = msg.user_id
        if not self._admin_user_ids or sender_id not in self._admin_user_ids:
            return Response(content="This command is admin-only.")
        if self._circuit_registry is None:
            return Response(content="Circuit breaker not configured.")
        all_status = self._circuit_registry.get_all_status()
        lines = ["Circuit Status", "─" * 38]
        for name, status in sorted(all_status.items()):
            if status.retry_after is not None:
                state_str = f"OPEN       retry in {int(status.retry_after)}s"
            else:
                state_str = f"{status.state.value.upper():<10} (ok)"
            lines.append(f"  {name:<12} {state_str}")
        lines.append("─" * 38)
        return Response(content="\n".join(lines))
